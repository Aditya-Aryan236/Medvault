"""Minimal GCP-center API (demo).

- Stores aggregates in a central \"Gold zone\" MongoDB.
- Stores consent status in Postgres.
- On consent change, creates a reaggregate task and notifies the hospital agent via webhook.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import time
from fastapi import FastAPI
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
import psycopg
import requests

app = FastAPI(title="MedVault Central API", version="0.1.0")

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name)
    if val is None or val == "":
        if default is not None:
            return default
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _gold_collection() -> Collection:
    uri = _env("GOLD_MONGODB_URI")
    db_name = _env("GOLD_MONGODB_DB", "gold")
    client = MongoClient(uri)
    return client[db_name]["aggregates"]


def _consent_db_uri() -> str:
    return _env("CONSENT_DB_URI")


def _ensure_postgres_schema() -> None:
    uri = _consent_db_uri()
    last_err: Exception | None = None
    for _ in range(30):
        try:
            with psycopg.connect(uri, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS consents (
                          hospital_code TEXT NOT NULL,
                          patient_id TEXT NOT NULL,
                          consent_active BOOLEAN NOT NULL,
                          updated_at TIMESTAMPTZ NOT NULL,
                          PRIMARY KEY (hospital_code, patient_id)
                        );
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS reaggregate_tasks (
                          id UUID PRIMARY KEY,
                          hospital_code TEXT NOT NULL,
                          patient_id TEXT NOT NULL,
                          status TEXT NOT NULL,
                          created_at TIMESTAMPTZ NOT NULL,
                          updated_at TIMESTAMPTZ NOT NULL,
                          last_error TEXT NULL
                        );
                        """
                    )
            return
        except Exception as e:  # noqa: BLE001 - demo-only
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"Failed to connect to consent-db after retries: {last_err}") from last_err


def _ensure_gold_indexes() -> None:
    col = _gold_collection()
    col.create_index([("hospital_code", ASCENDING), ("received_at", DESCENDING)])
    # Enforce one "latest" document per (hospital_code, kind).
    col.create_index([("hospital_code", ASCENDING), ("payload.kind", ASCENDING)], unique=True)


def _agent_url_for(hospital_code: str) -> str:
    # Minimal config: comma-separated mapping, e.g. "AMC=http://agent-amc:9000,CHU=http://agent-chu:9000"
    raw = os.environ.get("HOSPITAL_AGENT_URLS", "").strip()
    if raw:
        for pair in raw.split(","):
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            if k.strip().upper() == hospital_code.strip().upper():
                return v.strip().rstrip("/")

    # Fallback convention: agent-<CODE> -> http://agent-<code-lower>:9000
    return f"http://agent-{hospital_code.strip().lower()}:9000"


def _create_task(hospital_code: str, patient_id: str) -> uuid.UUID:
    task_id = uuid.uuid4()
    now = _utc_now()
    with psycopg.connect(_consent_db_uri(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reaggregate_tasks (id, hospital_code, patient_id, status, created_at, updated_at, last_error)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (task_id, hospital_code, patient_id, "created", now, now, None),
            )
    return task_id


def _set_task_status(task_id: uuid.UUID, status: str, last_error: str | None = None) -> None:
    now = _utc_now()
    with psycopg.connect(_consent_db_uri(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reaggregate_tasks
                SET status = %s, updated_at = %s, last_error = %s
                WHERE id = %s
                """,
                (status, now, last_error, task_id),
            )


@app.on_event("startup")
def _startup() -> None:
    _ensure_gold_indexes()
    _ensure_postgres_schema()


class AggregateIngest(BaseModel):
    hospital_code: str
    payload: dict[str, Any] | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest/aggregate")
def ingest_aggregate(body: AggregateIngest) -> dict[str, Any]:
    kind = (body.payload or {}).get("kind")
    if not kind:
        return {"ok": False, "error": "payload.kind is required for upsert"}

    entry = {
        "hospital_code": body.hospital_code,
        "received_at": _dt_iso(_utc_now()),
        "payload": body.payload or {},
    }
    col = _gold_collection()
    res = col.replace_one({"hospital_code": body.hospital_code, "payload.kind": kind}, entry, upsert=True)
    return {
        "ok": True,
        "upserted": bool(res.upserted_id is not None),
        "matched_count": res.matched_count,
        "modified_count": res.modified_count,
        "upserted_id": str(res.upserted_id) if res.upserted_id else None,
    }


@app.get("/ingest/aggregate/recent")
def recent_ingests(limit: int = 20) -> dict[str, Any]:
    lim = max(1, min(limit, 100))
    col = _gold_collection()
    docs = list(col.find({}, {"_id": 0}).sort("received_at", DESCENDING).limit(lim))
    return {"items": docs}


class ConsentUpdate(BaseModel):
    consent_active: bool


@app.get("/consents/{hospital_code}/{patient_id}")
def get_consent(hospital_code: str, patient_id: str) -> dict[str, Any]:
    with psycopg.connect(_consent_db_uri(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT consent_active, updated_at
                FROM consents
                WHERE hospital_code = %s AND patient_id = %s
                """,
                (hospital_code, patient_id),
            )
            row = cur.fetchone()
    if not row:
        return {"hospital_code": hospital_code, "patient_id": patient_id, "consent_active": None}
    consent_active, updated_at = row
    return {
        "hospital_code": hospital_code,
        "patient_id": patient_id,
        "consent_active": bool(consent_active),
        "updated_at": _dt_iso(updated_at),
    }


@app.put("/consents/{hospital_code}/{patient_id}")
def put_consent(hospital_code: str, patient_id: str, body: ConsentUpdate) -> dict[str, Any]:
    now = _utc_now()
    prev: bool | None = None
    with psycopg.connect(_consent_db_uri(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT consent_active
                FROM consents
                WHERE hospital_code = %s AND patient_id = %s
                """,
                (hospital_code, patient_id),
            )
            row = cur.fetchone()
            if row:
                prev = bool(row[0])

            cur.execute(
                """
                INSERT INTO consents (hospital_code, patient_id, consent_active, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (hospital_code, patient_id)
                DO UPDATE SET consent_active = EXCLUDED.consent_active, updated_at = EXCLUDED.updated_at
                """,
                (hospital_code, patient_id, body.consent_active, now),
            )

    changed = prev is None or prev != body.consent_active
    task_id: str | None = None
    webhook: dict[str, Any] | None = None

    if changed:
        t_id = _create_task(hospital_code, patient_id)
        task_id = str(t_id)
        agent_url = _agent_url_for(hospital_code)

        # 1) Update hospital-db consent (demo: hospital agent writes into its MongoDB).
        update_payload = {"consent_active": body.consent_active, "updated_at": _dt_iso(now), "source": "central"}
        # 2) Ask the agent to reaggregate and ingest back to central (demo-only).
        reagg_payload = {
            "hospital_code": hospital_code,
            "patient_id": patient_id,
            "reason": "consent_changed",
            "task_id": task_id,
            "consent_active": body.consent_active,
        }
        try:
            _set_task_status(t_id, "notifying")
            consent_r = requests.put(
                f"{agent_url}/patients/{patient_id}/consent",
                json=update_payload,
                timeout=5,
            )
            consent_r.raise_for_status()

            reagg_r = requests.post(f"{agent_url}/reaggregate", json=reagg_payload, timeout=15)
            reagg_r.raise_for_status()
            _set_task_status(t_id, "notified")
            webhook = {
                "ok": True,
                "agent_url": agent_url,
                "hospital_consent_update": consent_r.json() if consent_r.content else None,
                "reaggregate": reagg_r.json() if reagg_r.content else None,
            }
        except Exception as e:  # noqa: BLE001 - demo-only
            _set_task_status(t_id, "failed", str(e))
            webhook = {"ok": False, "agent_url": agent_url, "error": str(e)}

    return {
        "ok": True,
        "hospital_code": hospital_code,
        "patient_id": patient_id,
        "consent_active": body.consent_active,
        "changed": changed,
        "task_id": task_id,
        "webhook": webhook,
    }


@app.get("/tasks/reaggregate")
def list_reaggregate_tasks(limit: int = 50) -> dict[str, Any]:
    lim = max(1, min(limit, 200))
    with psycopg.connect(_consent_db_uri(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, hospital_code, patient_id, status, created_at, updated_at, last_error
                FROM reaggregate_tasks
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (lim,),
            )
            rows = cur.fetchall()
    items = []
    for (task_id, hospital_code, patient_id, status, created_at, updated_at, last_error) in rows:
        items.append(
            {
                "id": str(task_id),
                "hospital_code": hospital_code,
                "patient_id": patient_id,
                "status": status,
                "created_at": _dt_iso(created_at),
                "updated_at": _dt_iso(updated_at),
                "last_error": last_error,
            }
        )
    return {"items": items}


@app.get("/config/agents")
def agents_config() -> dict[str, str]:
    raw = os.environ.get("HOSPITAL_AGENTS", "")
    return {"HOSPITAL_AGENTS": raw}
