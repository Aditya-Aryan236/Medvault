// MedVault Demo Script – run inside mongosh (via mongos query router)
// Connect (host):      mongosh "mongodb://localhost:27017" demo.js
// Connect (in docker): docker exec -i hospital-UKL-router mongosh --port 27017 < demo.js
//
// Three operations matching the presentation plan:
//   1. (data ingest) – run scripts/mock-ingestion.js (Speed Layer simulation)
//   2. Aggregation   – HbA1c cohort query (the diabetes study)
//   3. Consent withdrawal – GDPR Art. 17 simulation

// ── Select database ───────────────────────────────────────────────────────
db = db.getSiblingDB('medvault');

// ═══════════════════════════════════════════════════════════════════════════
// OPERATION 1 — Ingestion (Speed Layer)
// This demo script no longer inserts data directly.
// Run the compose-based ingestor to continuously write new documents:
//
//   CENTRAL_API_URL=http://localhost:8000 ./demo/mock-ingestion.sh
//
// Then re-run this demo script to show aggregation + consent flow.
// ═══════════════════════════════════════════════════════════════════════════

print("\n── OP 1: Ingestion runs externally (Speed Layer mock-ingestion) ──");
var existing = db.patients.countDocuments();
print("Current medvault.patients count: " + existing);
if (existing < 10) {
  print("Tip: start ingestion first: `CENTRAL_API_URL=http://localhost:8000 ./demo/mock-ingestion.sh`");
}


// ═══════════════════════════════════════════════════════════════════════════
// OPERATION 2 — Aggregation pipeline: diabetes cohort HbA1c study
// Shows: $match (consent + ICD-10), $unwind, $group, $sort → no individual records
// ═══════════════════════════════════════════════════════════════════════════

print("\n── OP 2: Cohort aggregation – average HbA1c by treatment protocol ──");

var cohortResult = db.patients.aggregate([
  // Stage 1: only consented Type-2 diabetes patients
  { $match: {
      consent_active: true,
      icd10_primary:  { $in: ["E11.9", "E11.65", "E11.40", "E11.51"] }
  }},
  // Stage 2: flatten the embedded lab_results array
  { $unwind: "$lab_results" },
  // Stage 3: only followup measurements
  { $match: { "lab_results.phase": "followup" }},
  // Stage 4: group by protocol, compute aggregate stats
  { $group: {
      _id:               "$treatment_protocol",
      patient_count:     { $sum: 1 },
      avg_hba1c:         { $avg: "$lab_results.value" },
      min_hba1c:         { $min: "$lab_results.value" },
      max_hba1c:         { $max: "$lab_results.value" }
  }},
  // Stage 5: rank by best (lowest) average HbA1c
  { $sort: { avg_hba1c: 1 }},
  // Stage 6: round for readability
  { $project: {
      protocol:      "$_id",
      patient_count: 1,
      avg_hba1c:     { $round: ["$avg_hba1c", 2] },
      min_hba1c:     1,
      max_hba1c:     1,
      _id:           0
  }}
]).toArray();

print("Cohort query result (no individual records returned):");
printjson(cohortResult);
// Expected: 4 rows, one per protocol, with aggregated stats only


// ═══════════════════════════════════════════════════════════════════════════
// OPERATION 3 — Consent withdrawal: GDPR Art. 17 simulation
// Shows: consent_active flag → access revocation without physical deletion
// ═══════════════════════════════════════════════════════════════════════════

print("\n── OP 3: GDPR Art. 17 – consent withdrawal (example patient) ──");

// Pick one consented patient and output the Central API command to withdraw consent.
// (We do NOT mutate the hospital DB directly here; the real flow goes through central-api.)
var anyPatient = db.patients.findOne(
  { consent_active: true },
  { patient_id: 1, hospital_code: 1, consent_active: 1, _id: 0 }
);
if (!anyPatient || !anyPatient.patient_id) {
  print("No patient records found; ingestion may not be running yet.");
} else {
  print("Selected patient_id=" + anyPatient.patient_id + " hospital_code=" + (anyPatient.hospital_code || "__missing__"));
}

if (anyPatient && anyPatient.patient_id && anyPatient.hospital_code) {
  print("\nRun this from your host to trigger the full flow (consent-db -> hospital agent -> reaggregate -> gold):");
  print(
    'curl -X PUT "http://localhost:8000/consents/' +
      anyPatient.hospital_code +
      "/" +
      anyPatient.patient_id +
      '" -H "Content-Type: application/json" -d \'{"consent_active": false}\''
  );
  print("\nThen check:");
  print('- reaggregate tasks: curl "http://localhost:8000/tasks/reaggregate?limit=20"');
  print('- gold recent ingests: curl "http://localhost:8000/ingest/aggregate/recent?limit=20"');
  print('- gold UI: http://localhost:8086');
}

// ── Sharded cluster status check (good slide moment) ──────────────────────
// Note: rs.status() only works when connected directly to a mongod member.
// When connected to mongos (this demo), use sh.status() / listShards instead.
print("\n── Sharded cluster status summary (via mongos) ──");
try {
  var shards = db.adminCommand({ listShards: 1 });
  print("Shards:");
  printjson(shards);
  shards.shards.forEach(function(m) {
    print("host=" + m.host + "  state=" + m.state);
  });
  print("--------------------------------");
  print("Specific collection distribution:");
  printjson(db.patients.getShardDistribution());
} catch (e) {
  print("Could not run listShards (is this connected to mongos on 27017?): " + e);
}