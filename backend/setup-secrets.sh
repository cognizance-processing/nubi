#!/bin/bash
set -euo pipefail

ENV_FILE="${1:-.env.production}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found"
  echo "Usage: ./setup-secrets.sh [env-file]"
  echo "  Default: .env.production"
  exit 1
fi

PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
echo "Project: $PROJECT_ID"
echo "Reading secrets from: $ENV_FILE"
echo ""

while IFS= read -r line || [ -n "$line" ]; do
  # Skip comments and empty lines
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue

  KEY="${line%%=*}"
  KEY="${KEY%%[[:space:]]}"
  VALUE="${line#*=}"
  VALUE="${VALUE%%[[:space:]]}"
  VALUE="${VALUE%$'\r'}"

  # Skip if key or value is empty
  [[ -z "$KEY" || -z "$VALUE" ]] && continue

  echo -n "Creating secret: $KEY ... "

  # Create or update the secret (printf to avoid trailing newline)
  if gcloud secrets describe "$KEY" &>/dev/null; then
    printf '%s' "$VALUE" | gcloud secrets versions add "$KEY" --data-file=- --quiet
    echo "updated"
  else
    printf '%s' "$VALUE" | gcloud secrets create "$KEY" --data-file=- --quiet
    echo "created"
  fi
done < "$ENV_FILE"

echo ""
echo "Granting Cloud Run access to secrets..."

PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA="${PROJECT_NUM}-compute@developer.gserviceaccount.com"

while IFS= read -r line || [ -n "$line" ]; do
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  KEY="${line%%=*}"
  [[ -z "$KEY" ]] && continue

  gcloud secrets add-iam-policy-binding "$KEY" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet &>/dev/null

  echo "  Granted access: $KEY"
done < "$ENV_FILE"

echo ""
echo "Done! All secrets from $ENV_FILE are in GCP Secret Manager."
