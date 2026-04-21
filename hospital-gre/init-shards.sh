#!/bin/bash
# Ensure the script stops on error
# ── Replica set initialiser for Sharded Cluster ────────────────────────────
set -euo pipefail

echo "Waiting for MongoDB containers to start (10 seconds)..."
sleep 10

echo "1. Initialize Config Server Replica Set..."
docker exec hospital-gre-configsvr1 mongosh --port 27019 --eval 'rs.initiate({_id: "csrs", configsvr: true, members: [{_id: 0, host: "configsvr1:27019"}, {_id: 1, host: "configsvr2:27019"}, {_id: 2, host: "configsvr3:27019"}]})'

echo "2. Initialize Shard 1 Replica Set (include A, B and C nodes)..."
docker exec hospital-gre-shard1a mongosh --port 27018 --eval 'rs.initiate({_id: "sh1rs", members: [{_id: 0, host: "shard1a:27018"}, {_id: 1, host: "shard1b:27018"}, {_id: 2, host: "shard1c:27018"}]})'

echo "3. Initialize Shard 2 Replica Set (single node)..."
docker exec hospital-gre-shard2 mongosh --port 27018 --eval 'rs.initiate({_id: "sh2rs", members: [{_id: 0, host: "shard2:27018"}]})'

echo "Waiting for Replica Sets to elect Primary (15 seconds)..."
sleep 15

echo "4. Add Shards to Mongos router..."
docker exec hospital-gre-router mongosh --port 27017 --eval 'sh.addShard("sh1rs/shard1a:27018,shard1b:27018,shard1c:27018")'
docker exec hospital-gre-router mongosh --port 27017 --eval 'sh.addShard("sh2rs/shard2:27018")'

echo "✅ Sharded Cluster initialization completed!"

echo "5. set shard key for patients collection (pre-split chunks)..."
docker exec hospital-gre-router mongosh --port 27017 --eval '
  db = db.getSiblingDB("medvault");
  sh.enableSharding("medvault");
  // Use db.patients (as requested). If it was sharded before, drop and recreate sharding
  // metadata by dropping the collection first.
  try { db.patients.drop(); } catch (e) {}

  // Hashed shard key requires a matching hashed index.
  db.patients.createIndex({ patient_id: "hashed" });

  // Approach A: pre-split chunks at sharding time so data distributes across shards
  // without waiting for autosplit thresholds.
  sh.shardCollection(
    "medvault.patients",
    { patient_id: "hashed" },
    false,
    { numInitialChunks: 4 }
  );
'

echo "✅ Shard key set for patients collection!"