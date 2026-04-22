#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${DB_NAME:-medvault}"
COLLECTION="${COLLECTION:-patients}"

wait_for_mongo() {
  local name="$1"
  local host="$2"
  local port="$3"

  echo "Waiting for ${name} at ${host}:${port}..."
  for _ in $(seq 1 60); do
    if mongosh --host "${host}" --port "${port}" --quiet --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1; then
      echo "✅ ${name} is reachable."
      return 0
    fi
    sleep 2
  done

  echo "❌ Timed out waiting for ${name} (${host}:${port})"
  return 1
}

wait_for_mongo "mongos-router" "mongos-router" "27017"

echo "Registering shards on mongos (idempotent)..."
mongosh --host mongos-router --port 27017 --quiet --eval '
  try { sh.addShard("sh1rs/shard1:27018"); } catch (e) {}
  try { sh.addShard("sh2rs/shard2:27018"); } catch (e) {}
  printjson(db.adminCommand({ listShards: 1 }));
'

echo "Ensuring sharding enabled for ${DB_NAME}.${COLLECTION}..."
mongosh --host mongos-router --port 27017 --quiet --eval '
  const dbName = "'"${DB_NAME}"'";
  const coll = "'"${COLLECTION}"'";
  const dbx = db.getSiblingDB(dbName);

  try { sh.enableSharding(dbName); } catch (e) {}

  // Use hashed shard key on patient_id for uniform distribution.
  try { dbx.getCollection(coll).createIndex({ patient_id: "hashed" }); } catch (e) {}
  try {
    sh.shardCollection(dbName + "." + coll, { patient_id: "hashed" }, false, { numInitialChunks: 4 });
  } catch (e) {}
'

echo "✅ Simplified sharded cluster initialization completed."
