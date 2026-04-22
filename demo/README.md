# MedVault – G3 Demo Stack

MongoDB **sharded cluster** (CSRS + 2 shards) + Mongo Express  
Federated clinical research platform simulation (GDPR/HIPAA context)  
Group 3 · Data Platform Architectures · AIDA2

---

## What this stack does

Simulates the MedVault data platform from the case study:
- **Config Server Replica Set (CSRS)** — `configsvr1/2/3` (replica set id: `csrs`)
- **Shard 1 (replica set)** — `shard1a/1b/1c` (replica set id: `sh1rs`)
- **Shard 2 (replica set)** — `shard2` (replica set id: `sh2rs`)
- **Query router (mongos)** — `mongos-router` (container name: `hospital-UKL-router`) is the **only app entrypoint**
- **Write concern `w:majority`** — writes are acknowledged after majority of the targeted replica set(s)
- **Mongo Express UI** — browser-based interface to inspect the database through the router

This stack demonstrates **sharding** (horizontal scaling) on top of **replication** (fault tolerance).

Ports and host mappings are documented in [`PORTS.md`](../PORTS.md) (single source of truth).

---

## End-to-end demo goals (what you will show)

This runbook is designed to demonstrate:

1. Bring the full platform up (hospital shard + central services + agents) and start default ingestion.
2. Cohort aggregation automatically updates to `gcp-center` and can be viewed via UI/CLI.
3. Simulate **consent withdrawal** and show the data changes end-to-end.
4. Show **sharded cluster status** (prove sharding is configured).
5. Verify **replication sync** (secondary has the data).
6. Simulate a **single DB crash** (shard primary down) and keep operating.

---

## Prerequisites

**Docker Desktop** ≥ 4.x  
Download: https://www.docker.com/products/docker-desktop

**mongosh** (MongoDB Shell) — installed on your laptop separately  
Download: https://www.mongodb.com/try/download/shell  
Verify: `mongosh --version`

---

## Start the hospital sharded cluster (UKL)

```bash
docker compose -f docker-compose.yml up -d
```

Startup order (automatic via healthchecks):
1. Config servers + shard members start and pass their ping healthchecks
2. `mongos-router` starts after config servers are healthy
3. `init-shards-cluster` runs once to bootstrap the cluster (replica sets + `sh.addShard(...)` + shard key)
4. `mongo-express` starts after `mongos-router` is healthy

Wait ~30 seconds, then check:

```bash
docker compose -f docker-compose.yml ps
```

Key ports on your laptop:
- `mongodb://localhost:27017` → **mongos query router** (use this in apps/mongosh)
- `http://localhost:8081` → Mongo Express UI
- Optional (debug): `localhost:27019 / 28019 / 29019` → config servers

---

## Start the central services (gcp-center) + hospital agents

From the repo root:

```bash
./scripts/init-federated-network.sh
docker compose -f gcp-center/docker-compose.yml up -d
docker compose -f agents/docker-compose.yml up -d
```

Useful URLs:

- Central API (FastAPI): `http://localhost:8000`
- Gold UI (mongo-express for gold zone): `http://localhost:8086`
- Hospital agent (UKL): `http://localhost:9005`

---

## Start default ingestion (Speed Layer mock)

This continuously inserts new de-identified patients into each hospital database and also initializes consent entries in the central consent-db (default `consent_active=true` for newly ingested patients).

Run in a separate terminal:

```bash
CENTRAL_API_URL="http://localhost:8000" ./demo/mock-ingestion.sh
```

You should see logs like:

- `inserting N docs...`
- `consent-db initialized for N patients`

---

## Cluster initialization (automatic)

This stack automatically initializes the sharded cluster via the one-shot `init-shards-cluster` service (you’ll see a container named `hospital-UKL-init` start, run, then exit).

What it does:
- `rs.initiate()` for `csrs`, `sh1rs`, `sh2rs` (idempotent)
- `sh.addShard(...)` on the router (idempotent)
- Enables sharding for `medvault.patients` using a **hashed** shard key (`patient_id`) and pre-splits with `numInitialChunks: 4`

---

## Open the UI

`http://localhost:8081`  
Username / password are controlled by `.env`:
- `BASICAUTH_USERNAME`
- `BASICAUTH_PASSWORD`

---

## Run the demo script (DB-side view)

Make sure your terminal is in the same folder as `demo.js`, then:

```bash
mongosh "mongodb://localhost:27017" demo.js
```

If your laptop doesn't have `mongosh`, you can run the script using the router container's built-in `mongosh`:

```bash
docker exec -i hospital-UKL-router mongosh --port 27017 < demo.js
```

This script provides the **hospital DB-side** view:

- Shows that ingestion is happening (document count).
- Runs the cohort aggregation in MongoDB (no individual records returned).
- Prints a ready-to-run `curl` command to trigger **consent withdrawal via central-api** (end-to-end flow).

The script also prints the shard distribution for `patients` at the end.

---

## End-to-end demo: aggregation -> central -> consent withdrawal (CLI)

### 1) Verify aggregates arrive in gcp-center

Gold UI:

- `http://localhost:8086`

Or via CLI:

```bash
curl "http://localhost:8000/ingest/aggregate/recent?limit=20"
```

### 2) Consent withdrawal (end-to-end)

Pick one consented patient_id from the UKL hospital DB:

```bash
docker exec -i hospital-UKL-router mongosh --port 27017 --quiet --eval \
  'db=db.getSiblingDB("medvault"); print(db.patients.findOne({consent_active:true},{patient_id:1,hospital_code:1,_id:0}).patient_id)'
```

Withdraw consent via central-api (replace `<patient_id>`):

```bash
curl -X PUT "http://localhost:8000/consents/UKL/<patient_id>" \
  -H "Content-Type: application/json" \
  -d '{"consent_active": false}'
```

Observe task + gold changes:

```bash
curl "http://localhost:8000/tasks/reaggregate?limit=20"
curl "http://localhost:8000/ingest/aggregate/recent?limit=20"
```

Expected outcome:

- A new reaggregate task is created.
- The hospital agent updates hospital-db consent and recomputes the cohort aggregate.
- Central gold aggregate changes (counts/results reflect only consented patients).

---

## Sharded cluster status (prove shard config)

Run via mongos router:

```bash
docker exec -i hospital-UKL-router mongosh --port 27017 --quiet --eval 'db.adminCommand({listShards:1})'
docker exec -i hospital-UKL-router mongosh --port 27017 --quiet --eval 'sh.status()'
docker exec -i hospital-UKL-router mongosh --port 27017 --quiet --eval 'db=db.getSiblingDB("medvault"); printjson(db.patients.getShardDistribution())'
```

---

## Replication check (secondary has the data)

Connect to a shard member and check replica set status:

```bash
docker exec -i hospital-UKL-shard1b mongosh --port 27018 --quiet --eval 'rs.status().myState'
```

Run a read on a secondary (the count should match what you see via the router):

```bash
docker exec -i hospital-UKL-shard1b mongosh --port 27018 --quiet --eval \
  'rs.secondaryOk(); db=db.getSiblingDB("medvault"); print(db.patients.countDocuments())'
```

---

## Crash simulation: stop shard primary and keep operating

### 1) Identify the current primary (shard1 replica set)

```bash
docker exec -i hospital-UKL-shard1a mongosh --port 27018 --quiet --eval 'db.hello().isWritablePrimary'
docker exec -i hospital-UKL-shard1b mongosh --port 27018 --quiet --eval 'db.hello().isWritablePrimary'
docker exec -i hospital-UKL-shard1c mongosh --port 27018 --quiet --eval 'db.hello().isWritablePrimary'
```

The member returning `true` is the primary. Stop that container (example uses shard1a):

```bash
docker stop hospital-UKL-shard1a
```

### 2) Verify a new primary is elected

Re-run the `hello()` checks above, or view election state:

```bash
docker exec -i hospital-UKL-shard1b mongosh --port 27018 --quiet --eval 'rs.status().members.map(m=>({name:m.name,stateStr:m.stateStr}))'
```

### 3) Prove the system still works

- Ingestion should keep inserting (watch the ingestion terminal logs).
- Reads via router should still work:

```bash
docker exec -i hospital-UKL-router mongosh --port 27017 --quiet --eval 'db=db.getSiblingDB("medvault"); print(db.patients.countDocuments())'
```

### 4) Restore the stopped member

```bash
docker start hospital-UKL-shard1a
```

---

## Demo commands (short versions for live session)

Open mongosh interactively:

```bash
mongosh "mongodb://localhost:27017"
```

Then inside the shell:

```js
// Switch to database
db = db.getSiblingDB('medvault')

// Check document count
db.patients.countDocuments()

// Inspect one document
db.patients.findOne()

// Quick cohort query
db.patients.aggregate([
  { $match: { consent_active: true } },
  { $unwind: "$lab_results" },
  { $match: { "lab_results.phase": "followup" } },
  { $group: { _id: "$treatment_protocol", avg_hba1c: { $avg: "$lab_results.value" }, n: { $sum: 1 } } },
  { $sort: { avg_hba1c: 1 } }
])

// Consent withdrawal (use central-api so the full flow is exercised)
// 1) Pick a real patient_id:
//    docker exec -i hospital-UKL-router mongosh --port 27017 --quiet --eval \
//      'db=db.getSiblingDB("medvault"); print(db.patients.findOne({consent_active:true},{patient_id:1,_id:0}).patient_id)'
// 2) Withdraw via central-api:
//    curl -X PUT "http://localhost:8000/consents/UKL/<patient_id>" -H "Content-Type: application/json" -d '{"consent_active": false}'

// Sharded cluster status (via mongos)
db.adminCommand({ listShards: 1 })
sh.status()

// Distribution for the demo collection
db.patients.getShardDistribution()
```

---

## Stop the stack

```bash
# Stop containers, keep data (safe between practice runs)
docker compose -f docker-compose.yml down

# Full reset — wipe all data (use before a clean demo run-through)
docker compose -f docker-compose.yml down -v
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Error dependency mongos-router failed to start` / router unhealthy | Check `docker logs hospital-UKL-init` — cluster bootstrap must complete (CSRS must be initiated) before the router becomes healthy |
| Port `27019 already in use` | Something else is using `27019` on your laptop; stop it or change the host port mapping for `configsvr1` |
| Demo connects but `sh.status()` errors | Make sure you're connected to `mongodb://localhost:27017` (mongos), not a shard member |
| `mongosh: command not found` | Install from https://www.mongodb.com/try/download/shell |

---

## Architecture note — sharding vs replication

This stack implements both:
- **Replication** inside each replica set (`csrs`, `sh1rs`, `sh2rs`) for fault tolerance
- **Sharding** across shards for horizontal scaling

For a production-style design, a common shard key choice for `patients` is a **hashed** key (e.g. `hashed(patient_id)`) to reduce hotspotting compared to a range key with skewed demographics.
