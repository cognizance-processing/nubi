#!/usr/bin/env bash
#
# secrets.sh — push a Fly environment's secrets from a local env file (atomic set).
#
# Reads KEY=VALUE lines (ignores blanks and #comments) and sets them all in one
# `flyctl secrets set`, which triggers exactly one rollout. Real values live
# ONLY in these local files (gitignored) and in Fly — never committed. The dev
# and prod files should mirror each other except the per-env values (DATABASE_URL,
# CORS/public URLs). See ../.env.example.
#
# Environments:
#   main | prod  → app "nubi",     file ../.env       [default]
#   dev  | staging → app "nubi-dev", file ../.env.dev
#
# Usage:
#   scripts/secrets.sh            # push prod secrets (../.env → nubi)
#   scripts/secrets.sh dev        # push dev secrets  (../.env.dev → nubi-dev)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v flyctl >/dev/null 2>&1 || { echo "secrets: flyctl not found on PATH" >&2; exit 1; }

APP="nubi"; ENV_FILE="$HERE/.env"
case "${1:-main}" in
  main|prod|production) APP="nubi";     ENV_FILE="$HERE/.env" ;;
  dev|staging)          APP="nubi-dev"; ENV_FILE="$HERE/.env.dev" ;;
  *) echo "secrets: unknown environment '$1' (use: main | dev)" >&2; exit 1 ;;
esac

[ -f "$ENV_FILE" ] || { echo "secrets: no env file at $ENV_FILE (copy .env.example → $(basename "$ENV_FILE"))" >&2; exit 1; }

pairs=()
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue;; esac
  [[ "$line" == *"="* ]] || continue
  pairs+=("$line")
done < "$ENV_FILE"

[ "${#pairs[@]}" -gt 0 ] || { echo "secrets: no KEY=VALUE lines found in $ENV_FILE" >&2; exit 1; }

echo "secrets: setting ${#pairs[@]} secret(s) on app '$APP' from $(basename "$ENV_FILE")" >&2
exec flyctl secrets set --app "$APP" "${pairs[@]}"
