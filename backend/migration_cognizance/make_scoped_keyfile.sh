#!/usr/bin/env bash
# Create a LEAST-PRIVILEGE BigQuery service account + keyfile for the 10 migrated
# Cognizance dashboards. The SA can RUN query jobs in the project but can only READ
# the specific datasets the dashboards use — nothing else.
#
# Run this yourself (needs gcloud auth with admin on the project):
#   gcloud auth login
#   bash backend/migration_cognizance/make_scoped_keyfile.sh
#
# Output: ./keyfile.scoped.json  (point the connector's service_account_json at this)

set -euo pipefail

PROJECT="cog-analytics-etl-pipeline"
SA_NAME="nubi-cognizance-ro"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
KEY_OUT="$(dirname "$0")/keyfile.scoped.json"

# Datasets the 10 dashboards reference. KEYSTONE* is broad on purpose: 11 queries
# build the dataset name dynamically as KEYSTONE_<customer>.
STATIC_DATASETS=(
  QUINCE
  KEYSTONE_TESTNEW
  ADMIN
  KEYSTONE
  KEYSTONE_COKE
  KEYSTONE_PEPSICO_MOZAMBIQUE
  KEYSTONE_DKSH_HK
)

echo ">> Using project: $PROJECT"
gcloud config set project "$PROJECT" >/dev/null

# 1. Create the service account (idempotent)
if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Nubi Cognizance dashboards (read-only, scoped)"
else
  echo ">> Service account already exists: $SA_EMAIL"
fi

# 2. Project-level: jobUser ONLY (can run/bill query jobs; grants NO data access)
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/bigquery.jobUser" \
  --condition=None >/dev/null
echo ">> Granted roles/bigquery.jobUser at project scope"

# 3. Dataset-level: dataViewer (read-only) on each specific dataset only.
#    Uses the dataset's IAM policy so no other dataset is readable.
for DS in "${STATIC_DATASETS[@]}"; do
  if bq --project_id="$PROJECT" show "${PROJECT}:${DS}" >/dev/null 2>&1; then
    bq add-iam-policy-binding \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="roles/bigquery.dataViewer" \
      "${PROJECT}:${DS}" >/dev/null
    echo ">> Granted dataViewer on dataset: $DS"
  else
    echo "!! Dataset not found / no access, skipping: $DS"
  fi
done

# Optional, tighter: also grant any other KEYSTONE_* datasets (covers dynamic FROM).
# Uncomment to auto-grant the whole KEYSTONE_ family:
# while read -r DS; do
#   bq add-iam-policy-binding --member="serviceAccount:${SA_EMAIL}" \
#     --role="roles/bigquery.dataViewer" "${PROJECT}:${DS}" >/dev/null && echo ">> +$DS"
# done < <(bq ls --datasets --max_results=1000 "$PROJECT" | awk 'NR>2{print $1}' | grep '^KEYSTONE')

# 4. Mint the key
gcloud iam service-accounts keys create "$KEY_OUT" \
  --iam-account="$SA_EMAIL"
echo ""
echo ">> Wrote scoped keyfile: $KEY_OUT"
echo ">> This SA can run jobs in $PROJECT and read ONLY: ${STATIC_DATASETS[*]}"
echo ">> Keep keyfile.scoped.json out of git (already covered by .gitignore key*.json)."
