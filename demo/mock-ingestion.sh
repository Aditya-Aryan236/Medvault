#!/usr/bin/env bash
set -euo pipefail

INTERVAL_SECONDS="${INTERVAL_SECONDS:-30}"
INSERTS_PER_HOSPITAL="${INSERTS_PER_HOSPITAL:-10}"
DB_NAME="${DB_NAME:-medvault}"
COLLECTION="${COLLECTION:-patients}"
CENTRAL_API_URL="${CENTRAL_API_URL:-http://localhost:8000}"

declare -A URIS=(
  ["UKL"]="${MONGO_URI_UKL:-mongodb://host.docker.internal:27017}"
  ["AMC"]="${MONGO_URI_AMC:-mongodb://host.docker.internal:27117}"
  ["CHU"]="${MONGO_URI_CHU:-mongodb://host.docker.internal:27217}"
  ["UZG"]="${MONGO_URI_UZG:-mongodb://host.docker.internal:27247}"
  ["CHAR"]="${MONGO_URI_CHAR:-mongodb://host.docker.internal:27317}"
)

echo "Mock ingestion (Speed Layer) started."
echo "interval=${INTERVAL_SECONDS}s inserts_per_hospital=${INSERTS_PER_HOSPITAL} target=${DB_NAME}.${COLLECTION}"
echo "central_api_url=${CENTRAL_API_URL} (init consent-db per new patient)"

while true; do
  started_at="$(date -Iseconds)"
  batch_id="$(date -u +"%Y%m%d%H%M%S")"

  for code in "${!URIS[@]}"; do
    uri="${URIS[$code]}"
    echo "[$started_at] hospital=${code} uri=${uri} inserting ${INSERTS_PER_HOSPITAL} docs..."

    out="$(
      mongosh "${uri}" --quiet --eval '
      const hospitalCode = "'"${code}"'";
      const batchId = "'"${code}-${batch_id}"'";
      const dbName = "'"${DB_NAME}"'";
      const collectionName = "'"${COLLECTION}"'";
      const n = Number("'"${INSERTS_PER_HOSPITAL}"'");

      const protocols = ["Metformin-only", "Metformin+GLP1", "Insulin-basal", "Lifestyle-only"];
      const icd10codes = ["E11.9", "E11.65", "E11.40", "E11.51"];
      const languagesByHospital = { UKL: "de", AMC: "nl", CHU: "fr", CHAR: "de", UZG: "nl" };

      const now = new Date();
      const ts = now.toISOString().replace(/[-:.TZ]/g, "");
      const docs = [];

      function randSuffix() {
        return Math.random().toString(16).slice(2, 10);
      }

      for (let i = 0; i < n; i += 1) {
        const protocolIdx = i % protocols.length;
        const baseHbA1c = 7.2 + protocolIdx * 0.4 + (Math.random() * 0.8 - 0.4);
        const followupHbA1c = baseHbA1c - (0.3 + Math.random() * 0.6);
        const seq = String(i + 1).padStart(2, "0");

        docs.push({
          patient_id: `${hospitalCode}-PT-${ts}-${seq}-${randSuffix()}`,
          hospital_code: hospitalCode,
          age_bracket: ["45-54", "55-64", "65-74"][i % 3],
          biological_sex: i % 2 === 0 ? "M" : "F",
          icd10_primary: icd10codes[protocolIdx],
          treatment_protocol: protocols[protocolIdx],
          consent_active: true,
          note_language: languagesByHospital[hospitalCode] || "en",
          lab_results: [
            {
              test: "HbA1c",
              value: Number.parseFloat(baseHbA1c.toFixed(1)),
              unit: "%",
              date: new Date(now.getTime() - 1000 * 60 * 60 * 24 * 180),
              phase: "baseline"
            },
            {
              test: "HbA1c",
              value: Number.parseFloat(followupHbA1c.toFixed(1)),
              unit: "%",
              date: now,
              phase: "followup"
            }
          ],
          ingestion: {
            layer: "speed",
            batch_id: batchId,
            ingested_at: now
          }
        });
      }

      const dbx = db.getSiblingDB(dbName);
      const res = dbx.getCollection(collectionName).insertMany(docs, { writeConcern: { w: "majority" } });
      // Print inserted patient_ids for the caller (bash) to initialize consent-db.
      for (const d of docs) {
        print(`__patient_id__:${d.patient_id}`);
      }
      printjson({ hospital: hospitalCode, inserted: res.insertedIds ? Object.keys(res.insertedIds).length : 0, batch_id: batchId });
    '
    )" || {
      echo "[$started_at] hospital=${code} failed (will retry next tick)"
      continue
    }

    inserted=0
    while IFS= read -r line; do
      if [[ "${line}" == __patient_id__:* ]]; then
        patient_id="${line#__patient_id__:}"
        # Initialize consent-db: default consent_active=true for newly ingested patients (demo-only).
        curl -sS -X PUT "${CENTRAL_API_URL%/}/consents/${code}/${patient_id}" \
          -H "Content-Type: application/json" \
          -d '{"consent_active": true}' >/dev/null || true
        inserted=$((inserted + 1))
      fi
    done <<< "${out}"

    echo "[$started_at] hospital=${code} consent-db initialized for ${inserted} patients"
  done

  sleep "${INTERVAL_SECONDS}"
done
