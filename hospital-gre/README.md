# MedVault – G3 Demo Stack

MongoDB 3-node replica set + Mongo Express  
Federated clinical research platform simulation (GDPR/HIPAA context)  
Group 3 · Data Platform Architectures · AIDA2

---

## What this stack does

Simulates the MedVault data platform from the case study:
- **3-node MongoDB replica set** — primary (mongo1) + 2 secondaries (mongo2, mongo3)
- **Automatic failover** — if mongo1 goes down, mongo2 or mongo3 takes over
- **Write concern w:majority** — every write confirmed on 2 nodes before success
- **Mongo Express UI** — browser-based interface to inspect the database

This is replication, not sharding. All three nodes hold the same data. Purpose is fault tolerance and 30-year durability, not horizontal scale.

---

## Prerequisites

**Docker Desktop** ≥ 4.x  
Download: https://www.docker.com/products/docker-desktop

**mongosh** (MongoDB Shell) — installed on your laptop separately  
Download: https://www.mongodb.com/try/download/shell  
Verify: `mongosh --version`

---

## One-time laptop setup

MongoDB's replica set uses internal hostnames (`mongo1`, `mongo2`, `mongo3`) that only exist inside Docker. Your laptop needs to know what they mean. Run this once:

```bash
sudo sh -c "echo '127.0.0.1 mongo1
127.0.0.1 mongo2
127.0.0.1 mongo3' >> /etc/hosts"
```

It will ask for your laptop password. Verify it worked:

```bash
cat /etc/hosts | grep mongo
```

You should see all three lines.

---

## Start the stack

```bash
docker-compose up -d
```

Startup order (automatic via healthchecks):
1. `mongo1`, `mongo2`, `mongo3` start and pass their ping healthcheck
2. `mongo-init` runs `rs.initiate()`, waits for primary election, exits 0
3. `mongo-express` starts after `mongo-init` completes successfully

Wait ~30 seconds, then check:

```bash
docker-compose ps -a
```

Expected output:
```
medvault-express   Up (healthy)    0.0.0.0:8081->8081/tcp
medvault-init      Exited (0)
medvault-mongo1    Up (healthy)    0.0.0.0:27017->27017/tcp
medvault-mongo2    Up (healthy)    27017/tcp
medvault-mongo3    Up (healthy)    27017/tcp
```

`mongo-init` showing `Exited (0)` is correct — it is a one-shot setup container, not a server.

---

## Verify the replica set

```bash
docker exec -it medvault-mongo1 mongosh --eval "rs.status().members.forEach(m => print(m.name, m.stateStr))"
```

Expected:
```
mongo1:27017 PRIMARY
mongo2:27017 SECONDARY
mongo3:27017 SECONDARY
```

---

## Open the UI

http://localhost:8081  
Username: `admin`  
Password: `medvault2026`

---

## Run the demo script

Make sure your terminal is in the same folder as `demo.js`, then:

```bash
mongosh "mongodb://localhost:27017/?replicaSet=medvault-rs" demo.js
```

This runs three operations in sequence:
1. **insertMany** — inserts 50 de-identified patient documents with `w:majority`
2. **Aggregation** — cohort HbA1c query returning only anonymised statistics
3. **GDPR Art. 17** — consent withdrawal for PT-0003, then re-runs the same query to show exclusion

---

## Demo commands (short versions for live session)

Open mongosh interactively:

```bash
mongosh "mongodb://localhost:27017/?replicaSet=medvault-rs"
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
db.patients.updateOne({ patient_id: "PT-0003" }, { $set: { consent_active: false } })

// Replica set status
rs.status()
```

---

## Stop the stack

```bash
# Stop containers, keep data (safe between practice runs)
docker-compose down

# Full reset — wipe all data (use before a clean demo run-through)
docker-compose down -v
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `mongo-init` shows `Exited (0)` | This is correct — it is a one-shot container |
| `mongo-init` shows `Exited (1)` on rerun | Run `docker-compose logs mongo-init` — if it says "already initialized" the replica set is fine, just run `docker-compose down -v` and `up -d` for a clean start |
| `mongo-express` shows unhealthy but UI loads | Known cosmetic issue with the healthcheck — the UI works correctly, ignore the status |
| `MongoNetworkError: getaddrinfo ENOTFOUND mongo2` | You haven't added the hostnames to `/etc/hosts` — see One-time laptop setup above |
| `SyntaxError: Missing semicolon` on `use medvault` | Make sure line 10 of `demo.js` reads `db = db.getSiblingDB('medvault')` not `use medvault` |
| Port 27017 already in use | Stop any local MongoDB: `brew services stop mongodb-community` |
| `mongosh: command not found` | Install from https://www.mongodb.com/try/download/shell |

---

## Architecture note — replication vs sharding

This stack implements **replication** (fault tolerance) not **sharding** (horizontal scale).

In production MedVault, sharding would be added using `hash(patient_id)` as the shard key on the patients collection — chosen over `range(date_of_birth)` to avoid skew from the age bracket concentration in the diabetes study cohort (45–74 year olds). A sharded cluster would add config servers and a `mongos` router on top of this replica set foundation.
