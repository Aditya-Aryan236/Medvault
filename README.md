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
- **Query router (mongos)** — `mongos-router` (container name: `hospital-node-router`) is the **only app entrypoint**
- **Write concern `w:majority`** — writes are acknowledged after majority of the targeted replica set(s)
- **Mongo Express UI** — browser-based interface to inspect the database through the router

This stack demonstrates **sharding** (horizontal scaling) on top of **replication** (fault tolerance).

---

## Prerequisites

**Docker Desktop** ≥ 4.x  
Download: https://www.docker.com/products/docker-desktop

**mongosh** (MongoDB Shell) — installed on your laptop separately  
Download: https://www.mongodb.com/try/download/shell  
Verify: `mongosh --version`

---

## Start the stack

```bash
docker compose -f docker-compose.yml up -d
```

Startup order (automatic via healthchecks):
1. Config servers + shard members start and pass their ping healthchecks
2. `mongos-router` starts after config servers + shards are healthy
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

## Run the demo script

Make sure your terminal is in the same folder as `demo.js`, then:

```bash
mongosh "mongodb://localhost:27017" demo.js
```

If your laptop doesn't have `mongosh`, you can run the script using the router container's built-in `mongosh`:

```bash
docker exec -i hospital-node-router mongosh --port 27017 < demo.js
```

This runs three operations in sequence:
1. **insertMany** — inserts **100** de-identified patient documents with `w:majority` into `medvault.patients`
2. **Aggregation** — cohort HbA1c query returning only anonymised statistics
3. **GDPR Art. 17** — consent withdrawal for PT-000003, then re-runs the same query to show exclusion

The script also prints the shard distribution for `patients` at the end.

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

// Withdraw consent for PT-0003
db.patients.updateOne({ patient_id: "PT-000003" }, { $set: { consent_active: false } })

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
| `Error dependency mongos-router failed to start` / router unhealthy | Check `docker logs hospital-node-init` — cluster bootstrap must complete (CSRS must be initiated) before the router becomes healthy |
| Port `27019 already in use` | Something else is using `27019` on your laptop; stop it or change the host port mapping for `configsvr1` |
| Demo connects but `sh.status()` errors | Make sure you're connected to `mongodb://localhost:27017` (mongos), not a shard member |
| `mongosh: command not found` | Install from https://www.mongodb.com/try/download/shell |

---

## Architecture note — sharding vs replication

This stack implements both:
- **Replication** inside each replica set (`csrs`, `sh1rs`, `sh2rs`) for fault tolerance
- **Sharding** across shards for horizontal scaling

For a production-style design, a common shard key choice for `patients` is a **hashed** key (e.g. `hashed(patient_id)`) to reduce hotspotting compared to a range key with skewed demographics.