// MedVault Demo Script – run inside mongosh (via mongos query router)
// Connect (host):      mongosh "mongodb://localhost:27017" demo.js
// Connect (in docker): docker exec -i hospital-gre-router mongosh --port 27017 < demo.js
//
// Three operations matching the presentation plan:
//   1. insertMany  – batch ingest 50 de-identified patient docs
//   2. Aggregation – HbA1c cohort query (the diabetes study)
//   3. Consent withdrawal – GDPR Art. 17 simulation

// ── Select database ───────────────────────────────────────────────────────
db = db.getSiblingDB('medvault');

// ═══════════════════════════════════════════════════════════════════════════
// OPERATION 1 — insertMany: batch ingest de-identified patient records
// Shows: document model (embedded lab results), anonymised IDs, replica write
// ═══════════════════════════════════════════════════════════════════════════

print("\n── OP 1: Inserting 100 de-identified patient documents ──");

var patients = [];
var protocols  = ["Metformin-only", "Metformin+GLP1", "Insulin-basal", "Lifestyle-only"];
var icd10codes = ["E11.9", "E11.65", "E11.40", "E11.51"]; // Type-2 diabetes variants
var hospitals  = ["UKL", "AMC", "CHU-Paris", "Charité", "UZG"];
var languages  = ["de", "nl", "fr", "de", "nl"];

for (var i = 1; i <= 100; i++) {
  var protocolIdx   = (i - 1) % 4; // 0,1,2,3 each of the 4 protocols gets 12-13 patients 
  var hospitalIdx   = (i - 1) % 5; // 0,1,2,3,4 each of the 5 hospitals gets 10 patients
  var baseHbA1c     = 7.2 + (protocolIdx * 0.4) + (Math.random() * 0.8 - 0.4);
  var followupHbA1c = baseHbA1c - (0.3 + Math.random() * 0.6);

  patients.push({
    patient_id:       "PT-" + String(i).padStart(4, "0"),   // anonymised
    hospital_code:    hospitals[hospitalIdx],
    age_bracket:      ["45-54", "55-64", "65-74"][(i % 3)],
    biological_sex:   i % 2 === 0 ? "M" : "F",
    icd10_primary:    icd10codes[protocolIdx],
    treatment_protocol: protocols[protocolIdx],
    consent_active:   true,                                  // GDPR consent flag
    note_language:    languages[hospitalIdx],
    lab_results: [                                           // embedded sub-docs
      {
        test:       "HbA1c",
        value:      parseFloat(baseHbA1c.toFixed(1)),
        unit:       "%",
        date:       new Date("2023-01-15"),
        phase:      "baseline"
      },
      {
        test:       "HbA1c",
        value:      parseFloat(followupHbA1c.toFixed(1)),
        unit:       "%",
        date:       new Date("2023-07-15"),
        phase:      "followup"
      }
    ],
    genomic_id:       "GEN-" + String(i).padStart(6, "0"),  // reference to GridFS doc
    ingested_at:      new Date()
  });
}

var result = db.patients.insertMany(patients, { writeConcern: { w: "majority" } });
// w:"majority" = write confirmed on primary + at least one secondary before ack
print("Inserted: " + Object.keys(result.insertedIds).length + " documents");
print("writeConcern w:majority – confirmed on primary + secondary");

// Verify replication (run on secondary to confirm data arrived)
// rs.secondaryOk(); db.patients.countDocuments();

// ═══════════════════════════════════════════════════════════════════════════
// OPERATION 1.5 — createIndex: optimise queries before running aggregation
// Shows: compound index on consent + ICD-10 (matches $match in Op 2)
// ═══════════════════════════════════════════════════════════════════════════

print("\n── OP 1.5: Creating indexes for consent + ICD-10 queries ──");

db.patients.createIndex(
  { consent_active: 1, icd10_primary: 1 },
  { name: "idx_consent_icd10" }
);

db.patients.createIndex(
  { patient_id: 1 },
  { unique: true, name: "idx_patient_id_unique" }
);

print("Indexes created:");
printjson(db.patients.getIndexes().map(i => i.name));

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

print("\n── OP 3: GDPR Art. 17 – consent withdrawal for PT-0003 ──");

// Patient PT-0003 withdraws consent
var withdraw = db.patients.updateOne(
  { patient_id: "PT-0003" },
  {
    $set: {
      consent_active:    false,
      consent_withdrawn: new Date(),
      withdrawal_reason: "patient_request"   // audit field
    }
  },
  { writeConcern: { w: "majority" } }
);
print("Consent withdrawn. Modified count: " + withdraw.modifiedCount);

// Confirm the record still exists (data not deleted – legal archive intact)
var archived = db.patients.findOne(
  { patient_id: "PT-0003" },
  { patient_id: 1, consent_active: 1, consent_withdrawn: 1, _id: 0 }
);
print("Record still in archive (for legal retention):");
printjson(archived);

// Re-run the SAME cohort query – PT-0003 is now invisible to researchers
print("\nRe-running cohort query after withdrawal:");
var afterWithdrawal = db.patients.aggregate([
  { $match: {
      consent_active: true,                   // ← same filter, now excludes PT-0003
      icd10_primary:  { $in: ["E11.9", "E11.65", "E11.40", "E11.51"] }
  }},
  { $unwind: "$lab_results" },
  { $match: { "lab_results.phase": "followup" }},
  { $group: {
      _id:           "$treatment_protocol",
      patient_count: { $sum: 1 },
      avg_hba1c:     { $avg: "$lab_results.value" }
  }},
  { $sort: { avg_hba1c: 1 }},
  { $project: {
      protocol:      "$_id",
      patient_count: 1,
      avg_hba1c:     { $round: ["$avg_hba1c", 2] },
      _id:           0
  }}
]).toArray();

print("Post-withdrawal result (PT-0003 excluded – count reduced by 1 in its protocol):");
printjson(afterWithdrawal);

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
