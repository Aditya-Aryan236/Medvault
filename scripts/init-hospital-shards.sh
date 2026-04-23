
#!/bin/bash
# Ensure the script stops on error
# ── Replica set initialiser for Sharded Cluster ────────────────────────────
set -euo pipefail

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

rs_initiate_if_needed() {
  local name="$1"
  local host="$2"
  local port="$3"
  local initiate_js="$4"

  echo "Ensuring replica set for ${name} is initiated..."
  if mongosh --host "${host}" --port "${port}" --quiet --eval 'rs.status().ok' >/dev/null 2>&1; then
    echo "✅ ${name} replica set already initiated."
    return 0
  fi

  # rs.status() throws when not initiated; attempt initiate and tolerate "already initialized" cases.
  mongosh --host "${host}" --port "${port}" --eval "${initiate_js}" || true
}

wait_for_mongo "configsvr1" "configsvr1" "27019"
wait_for_mongo "configsvr2" "configsvr2" "27019"
wait_for_mongo "configsvr3" "configsvr3" "27019"
wait_for_mongo "shard1a" "shard1a" "27018"
wait_for_mongo "shard1b" "shard1b" "27018"
wait_for_mongo "shard1c" "shard1c" "27018"
wait_for_mongo "shard2" "shard2" "27018"

echo "1. Initialize Config Server Replica Set..."
rs_initiate_if_needed \
  "csrs" \
  "configsvr1" \
  "27019" \
  'rs.initiate({_id: "csrs", configsvr: true,
                members: [{_id: 0, host: "configsvr1:27019"},
                          {_id: 1, host: "configsvr2:27019"},
                          {_id: 2, host: "configsvr3:27019"}]})'

echo "2. Initialize Shard 1 Replica Set (include A, B and C nodes)..."
rs_initiate_if_needed \
  "sh1rs" \
  "shard1a" \
  "27018" \
  'rs.initiate({_id: "sh1rs",
                members: [{_id: 0, host: "shard1a:27018"},
                          {_id: 1, host: "shard1b:27018"},
                          {_id: 2, host: "shard1c:27018"}]})'

echo "3. Initialize Shard 2 Replica Set (single node)..."
rs_initiate_if_needed \
  "sh2rs" \
  "shard2" \
  "27018" \
  'rs.initiate({_id: "sh2rs",
                members: [{_id: 0, host: "shard2:27018"}]})'

echo "Waiting for Replica Sets to elect Primary (15 seconds)..."
sleep 15

wait_for_mongo "mongos-router" "mongos-router" "27017"

echo "4. Add Shards to Mongos router..."
mongosh --host mongos-router --port 27017 --eval '
      sh.addShard("sh1rs/shard1a:27018,shard1b:27018,shard1c:27018")
      ' || true
mongosh --host mongos-router --port 27017 --eval '
      sh.addShard("sh2rs/shard2:27018")
      ' || true

echo "✅ Sharded Cluster initialization completed!"

echo "5. set shard key for patients collection (pre-split chunks)..."
mongosh --host mongos-router --port 27017 --eval '
  db = db.getSiblingDB("medvault");
  sh.enableSharding("medvault");

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