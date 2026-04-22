#!/bin/bash
# Replica sets are created by compose (init-replica-sets). This script registers shards and sets the shard key.
set -euo pipefail

echo "1. Add Shards to Mongos router..."
docker exec hospital-AMC-router mongosh --port 27017 --eval 'sh.addShard("sh1rs/shard1:27018")'
docker exec hospital-AMC-router mongosh --port 27017 --eval 'sh.addShard("sh2rs/shard2:27018")'

echo "✅ Sharded Cluster initialization completed!"

echo "2. Set shard key for patients collection (pre-split chunks)..."
docker exec hospital-AMC-router mongosh --port 27017 --eval '
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