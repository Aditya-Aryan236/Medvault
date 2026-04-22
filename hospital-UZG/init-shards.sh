#!/bin/bash
# Replica sets: compose init-replica-sets. This script: add shards + shard key.
set -euo pipefail

echo "1. Add Shards to Mongos router..."
docker exec hospital-UZG-router mongosh --port 27017 --eval 'sh.addShard("sh1rs/shard1:27018")'
docker exec hospital-UZG-router mongosh --port 27017 --eval 'sh.addShard("sh2rs/shard2:27018")'

echo "✅ Sharded Cluster initialization completed!"

echo "2. Set shard key for patients collection (pre-split chunks)..."
docker exec hospital-UZG-router mongosh --port 27017 --eval '
  db = db.getSiblingDB("medvault");
  sh.enableSharding("medvault");
  try { db.patients.drop(); } catch (e) {}
  db.patients.createIndex({ patient_id: "hashed" });
  sh.shardCollection(
    "medvault.patients",
    { patient_id: "hashed" },
    false,
    { numInitialChunks: 4 }
  );
'

echo "✅ Shard key set for patients collection!"
