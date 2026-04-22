# gcp-center (minimal demo v2)

This stack simulates a \"central\" platform without any real GCP services:

- `gcp-api` (FastAPI): receives per-hospital aggregates and writes them to a central Gold MongoDB.
- `gold-mongo`: stores aggregates (Gold zone).
- `gold-express`: user-friendly UI to browse/search the Gold zone.
- `consent-db` (Postgres): stores patient consent status and reaggregate tasks.

Ports and host mappings are documented in [`PORTS.md`](../PORTS.md) (single source of truth).

## Start

From repo root:

```bash
./scripts/init-federated-network.sh
docker compose \
  --env-file ./.shared.env \
  --env-file gcp-center/.env \
  -f gcp-center/docker-compose.yml \
  up -d --build

# optional (only needed for webhook demo)
docker compose -f agents/docker-compose.yml up -d --build
```

## Demo flow (consent -> webhook -> reaggregate -> gold)

1. Choose a real `patient_id` (example: UKL hospital DB via mongos router):

```bash
docker exec -i hospital-UKL-router mongosh --port 27017 --quiet --eval \
  'db=db.getSiblingDB("medvault"); print(db.patients.findOne({consent_active:true},{patient_id:1,hospital_code:1,_id:0}).patient_id)'
```

2. Update consent (central updates consent-db, then tells the matching hospital agent to update hospital-db + reaggregate).

```bash
curl -X PUT "http://localhost:8000/consents/UKL/<patient_id>" \
  -H "Content-Type: application/json" \
  -d '{"consent_active": false}'
```

3. Inspect reaggregate tasks:

```bash
curl "http://localhost:8000/tasks/reaggregate?limit=20"
```

4. Inspect the Gold zone:

- UI: `http://localhost:8086`
- Or list recent ingests:

```bash
curl "http://localhost:8000/ingest/aggregate/recent?limit=20"
```

You should see new documents in the `gold.aggregates` collection.

For the full end-to-end runbook (ingestion + sharding/replica/crash demos), see [`demo/README.md`](../demo/README.md).

