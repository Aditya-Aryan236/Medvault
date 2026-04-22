from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI
from pydantic import BaseModel

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


class ReaggregateRequest(BaseModel):
    hospital_code: str
    patient_id: str
    reason: str | None = None
    task_id: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reaggregate")
def reaggregate(body: ReaggregateRequest) -> dict[str, Any]:
    # Demo-only: simulate recomputation by producing a small aggregate payload.
    hospital_code = body.hospital_code or _env("HOSPITAL_CODE")
    central_url = _env("CENTRAL_API_URL", "http://gcp-api:8000").rstrip("/")

    payload = {
        "kind": "demo_aggregate",
        "patient_id": body.patient_id,
        "consent_reason": body.reason or "unknown",
        "recomputed_at": _utc_now_iso(),
    }

    ingest_body = {"hospital_code": hospital_code, "payload": payload}
    r = requests.post(f"{central_url}/ingest/aggregate", json=ingest_body, timeout=5)
    r.raise_for_status()

    return {"ok": True, "central_ingest": r.json()}

