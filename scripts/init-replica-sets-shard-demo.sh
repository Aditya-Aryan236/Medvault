#!/usr/bin/env bash
# One-shot bootstrap for simplified shard demos (AMC, CHAR, CHU, UZG):
# configsvr + shard replica sets must exist before mongos can listen on 27017
# and pass Docker healthchecks.
set -euo pipefail

mongosh --host configsvr1 --port 27019 --quiet --eval '
try { if (rs.status().ok) quit(0); } catch (e) {}
rs.initiate({
  _id: "csrs",
  configsvr: true,
  members: [{ _id: 0, host: "configsvr1:27019" }]
});
'

mongosh --host shard1 --port 27018 --quiet --eval '
try { if (rs.status().ok) quit(0); } catch (e) {}
rs.initiate({
  _id: "sh1rs",
  members: [{ _id: 0, host: "shard1:27018" }]
});
'

mongosh --host shard2 --port 27018 --quiet --eval '
try { if (rs.status().ok) quit(0); } catch (e) {}
rs.initiate({
  _id: "sh2rs",
  members: [{ _id: 0, host: "shard2:27018" }]
});
'

sleep 5
