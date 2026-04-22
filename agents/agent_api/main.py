from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI
from pydantic import BaseModel
from pymongo import MongoClient

app = FastAPI(title="MedVault Mock Hospital Agent", version="0.1.0")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name)
    if val is None or val == "":
        if default is not None:
            return default
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


class ReaggregateRequest(BaseModel):
    hospital_code: str
    patient_id: str
    reason: str | None = None
    task_id: str | None = None
    consent_active: bool | None = None


class PatientConsentUpdate(BaseModel):
    consent_active: bool
    updated_at: str | None = None
    source: str | None = None


_cohort_lock = threading.Lock()
_latest_cohort: dict[str, Any] | None = None
_cohort_thread: threading.Thread | None = None
_cohort_stop = threading.Event()

_cs_thread: threading.Thread | None = None
_cs_stop = threading.Event()
_debounce_thread: threading.Thread | None = None
_debounce_stop = threading.Event()
_debounce_lock = threading.Lock()
_debounce_dirty = False
_debounce_last_event_at: float | None = None  # monotonic seconds
_initial_thread: threading.Thread | None = None


def _mongo_client() -> MongoClient:
    uri = _env("MONGO_URI", "mongodb://mongos-router:27017")
    return MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)


def _cohort_pipeline() -> list[dict[str, Any]]:
    # Mirrors demo/demo.js aggregation (HbA1c followup, consented T2D cohort).
    return [
        {"$match": {"consent_active": True, "icd10_primary": {"$in": ["E11.9", "E11.65", "E11.40", "E11.51"]}}},
        {"$unwind": "$lab_results"},
        {"$match": {"lab_results.phase": "followup"}},
        {
            "$group": {
                "_id": "$treatment_protocol",
                "patient_count": {"$sum": 1},
                "avg_hba1c": {"$avg": "$lab_results.value"},
                "min_hba1c": {"$min": "$lab_results.value"},
                "max_hba1c": {"$max": "$lab_results.value"},
            }
        },
        {"$sort": {"avg_hba1c": 1}},
        {
            "$project": {
                "_id": 0,
                "protocol": "$_id",
                "patient_count": 1,
                # Round to 2 decimals for presentation consistency.
                "avg_hba1c": {"$round": ["$avg_hba1c", 2]},
                "min_hba1c": 1,
                "max_hba1c": 1,
            }
        },
    ]


def _cohort_match_filter() -> dict[str, Any]:
    # Cohort matched total should reflect the same filters as the pipeline.
    return {"consent_active": True, "icd10_primary": {"$in": ["E11.9", "E11.65", "E11.40", "E11.51"]}, "lab_results.phase": "followup"}


def _run_cohort_once() -> dict[str, Any]:
    hospital_code = _env("HOSPITAL_CODE")
    central_url = _env("CENTRAL_API_URL", "http://gcp-api:8000").rstrip("/")
    db_name = _env("MONGO_DB", "medvault")
    coll_name = _env("MONGO_COLLECTION", "patients")

    computed_at = _utc_now_iso()
    client = _mongo_client()
    try:
        coll = client[db_name][coll_name]
        patients_total = coll.estimated_document_count()
        cohort_matched_total = coll.count_documents(_cohort_match_filter())
        rows = list(coll.aggregate(_cohort_pipeline(), allowDiskUse=True))
    finally:
        client.close()

    payload = {
        "kind": "cohort_hba1c_by_protocol",
        "hospital_code": hospital_code,
        "computed_at": computed_at,
        "patients_total": int(patients_total),
        "cohort_matched_total": int(cohort_matched_total),
        "result": rows,
        "meta": {
            "db": db_name,
            "collection": coll_name,
            "pipeline": "demo_v1",
        },
    }

    ingest_body = {"hospital_code": hospital_code, "payload": payload}
    r = requests.post(f"{central_url}/ingest/aggregate", json=ingest_body, timeout=8)
    r.raise_for_status()
    central_resp = r.json()

    out = {"ok": True, "computed_at": computed_at, "rows": len(rows), "central_ingest": central_resp}
    with _cohort_lock:
        global _latest_cohort
        _latest_cohort = {"summary": out, "payload": payload}
    return out


def _cohort_loop(interval_seconds: int) -> None:
    # Run immediately, then every interval. Never crash the agent process.
    while not _cohort_stop.is_set():
        try:
            res = _run_cohort_once()
            print(f"[cohort] ok rows={res['rows']} computed_at={res['computed_at']}", flush=True)
        except Exception as e:  # noqa: BLE001 - demo-only
            print(f"[cohort] failed: {e}", flush=True)
        _cohort_stop.wait(interval_seconds)


def _update_affects_cohort(updated_fields: dict[str, Any] | None, removed_fields: list[str] | None) -> bool:
    # Keep demo simple: only react to fields that can change the cohort.
    interesting_prefixes = (
        "consent_active",
        "icd10_primary",
        "treatment_protocol",
        "lab_results",
    )
    for k in (updated_fields or {}).keys():
        if k == "consent_active":
            return True
        if k.startswith(interesting_prefixes):
            return True
    for k in (removed_fields or []):
        if k == "consent_active":
            return True
        if k.startswith(interesting_prefixes):
            return True
    return False


def _mark_debounce_dirty() -> None:
    now = time.monotonic()
    with _debounce_lock:
        global _debounce_dirty, _debounce_last_event_at
        _debounce_dirty = True
        _debounce_last_event_at = now


def _change_stream_loop(db_name: str, coll_name: str, full_document: str | None) -> None:
    # Watch collection changes (through mongos/router) and mark dirty when relevant fields change.
    # Never crash the agent process.
    pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}]
    backoff_seconds = 1.0
    while not _cs_stop.is_set():
        client = _mongo_client()
        try:
            coll = client[db_name][coll_name]
            kwargs: dict[str, Any] = {}
            if full_document:
                kwargs["full_document"] = full_document

            with coll.watch(pipeline, **kwargs) as stream:
                print("[change-stream] watching for changes", flush=True)
                backoff_seconds = 1.0
                for change in stream:
                    if _cs_stop.is_set():
                        break
                    op = change.get("operationType")
                    if op in {"insert", "replace"}:
                        _mark_debounce_dirty()
                        continue
                    if op == "update":
                        desc = change.get("updateDescription") or {}
                        updated_fields = desc.get("updatedFields") or {}
                        removed_fields = desc.get("removedFields") or []
                        if _update_affects_cohort(updated_fields, removed_fields):
                            _mark_debounce_dirty()
        except Exception as e:  # noqa: BLE001 - demo-only
            print(f"[change-stream] failed: {e} (retrying)", flush=True)
            _cs_stop.wait(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 30.0)
        finally:
            client.close()


def _debounce_loop(debounce_seconds: int) -> None:
    # Debounce changes: only recompute once changes settle for debounce_seconds.
    while not _debounce_stop.is_set():
        should_run = False
        with _debounce_lock:
            dirty = _debounce_dirty
            last_at = _debounce_last_event_at
        if dirty and last_at is not None:
            if time.monotonic() - last_at >= debounce_seconds:
                should_run = True

        if should_run:
            try:
                res = _run_cohort_once()
                print(f"[debounce] ok rows={res['rows']} computed_at={res['computed_at']}", flush=True)
                with _debounce_lock:
                    global _debounce_dirty
                    _debounce_dirty = False
            except Exception as e:  # noqa: BLE001 - demo-only
                print(f"[debounce] failed: {e}", flush=True)
                # Keep dirty so it will retry after a short pause.
                _debounce_stop.wait(5)
                continue

        _debounce_stop.wait(1)


@app.on_event("startup")
def _start_cohort_scheduler() -> None:
    change_stream_enabled = _env_bool("CHANGE_STREAM_ENABLED", True)
    db_name = _env("MONGO_DB", "medvault")
    coll_name = _env("MONGO_COLLECTION", "patients")

    if change_stream_enabled:
        debounce_seconds = int(_env("CHANGE_STREAM_DEBOUNCE_SECONDS", "30"))
        full_document = os.environ.get("CHANGE_STREAM_FULL_DOCUMENT", "updateLookup").strip() or None

        global _cs_thread, _debounce_thread, _initial_thread
        if not (_cs_thread and _cs_thread.is_alive()):
            _cs_stop.clear()
            _cs_thread = threading.Thread(
                target=_change_stream_loop,
                args=(db_name, coll_name, full_document),
                daemon=True,
            )
            _cs_thread.start()
        if not (_debounce_thread and _debounce_thread.is_alive()):
            _debounce_stop.clear()
            _debounce_thread = threading.Thread(
                target=_debounce_loop,
                args=(debounce_seconds,),
                daemon=True,
            )
            _debounce_thread.start()
        if not (_initial_thread and _initial_thread.is_alive()):
            def _initial_compute() -> None:
                try:
                    res = _run_cohort_once()
                    print(f"[startup] cohort ok rows={res['rows']} computed_at={res['computed_at']}", flush=True)
                except Exception as e:  # noqa: BLE001 - demo-only
                    print(f"[startup] cohort failed: {e}", flush=True)

            _initial_thread = threading.Thread(target=_initial_compute, daemon=True)
            _initial_thread.start()

    # Prefer event-driven updates; keep interval scheduler optional as fallback.
    interval_fallback_enabled = _env_bool("COHORT_INTERVAL_FALLBACK_ENABLED", False)
    if not interval_fallback_enabled:
        return

    interval = int(_env("COHORT_INTERVAL_SECONDS", "60"))
    if interval <= 0:
        return
    global _cohort_thread
    if _cohort_thread and _cohort_thread.is_alive():
        return
    _cohort_stop.clear()
    _cohort_thread = threading.Thread(target=_cohort_loop, args=(interval,), daemon=True)
    _cohort_thread.start()


@app.on_event("shutdown")
def _stop_cohort_scheduler() -> None:
    _cohort_stop.set()
    t = _cohort_thread
    if t:
        t.join(timeout=2)

    _cs_stop.set()
    _debounce_stop.set()
    t2 = _cs_thread
    if t2:
        t2.join(timeout=2)
    t3 = _debounce_thread
    if t3:
        t3.join(timeout=2)
    t4 = _initial_thread
    if t4:
        t4.join(timeout=2)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/cohort/latest")
def cohort_latest() -> dict[str, Any]:
    with _cohort_lock:
        if _latest_cohort is None:
            return {"ok": False, "error": "no cohort computed yet"}
        return {"ok": True, **_latest_cohort}


@app.put("/patients/{patient_id}/consent")
def put_patient_consent(patient_id: str, body: PatientConsentUpdate) -> dict[str, Any]:
    db_name = _env("MONGO_DB", "medvault")
    coll_name = _env("MONGO_COLLECTION", "patients")
    updated_at = body.updated_at or _utc_now_iso()
    source = body.source or "central"

    client = _mongo_client()
    try:
        coll = client[db_name][coll_name]
        res = coll.update_one(
            {"patient_id": patient_id},
            {
                "$set": {
                    "consent_active": bool(body.consent_active),
                    "consent_updated_at": updated_at,
                    "consent_source": source,
                }
            },
        )
    finally:
        client.close()

    return {
        "ok": True,
        "patient_id": patient_id,
        "consent_active": bool(body.consent_active),
        "consent_updated_at": updated_at,
        "consent_source": source,
        "matched_count": int(res.matched_count),
        "modified_count": int(res.modified_count),
    }


@app.post("/reaggregate")
def reaggregate(body: ReaggregateRequest) -> dict[str, Any]:
    # Demo-only: recompute cohort aggregate (consented-only) then ingest to central.
    # The cohort pipeline filters `consent_active: True`, so withdrawals reduce counts.
    res = _run_cohort_once()
    return {
        "ok": True,
        "reason": body.reason,
        "task_id": body.task_id,
        "patient_id": body.patient_id,
        "consent_active": body.consent_active,
        "reaggregate": res,
    }

