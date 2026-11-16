#!/usr/bin/env bash
#
# secrets.sh — push secrets from a local env file to a Fly app in ONE atomic
# `flyctl secrets set` (= exactly one rollout). Reads KEY=VALUE lines, skips
# blanks and #comments. Real values live only in the (gitignored) env file and
# in Fly — never in git. Template: deploy/fly/.env.fly.example.
#
#   NUBI_FLY_APP=my-nubi deploy/fly/secrets.sh .env.fly
set -euo pipefail

command -v flyctl >/dev/null 2>&1 || { echo "secrets: flyctl not found on PATH" >&2; exit 1; }
APP="${NUBI_FLY_APP:?set NUBI_FLY_APP to the Fly app name}"
ENV_FILE="${1:?usage: secrets.sh <env-file>}"
[ -f "$ENV_FILE" ] || { echo "secrets: no env file at $ENV_FILE" >&2; exit 1; }

pairs=()
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue;; esac
  [[ "$line" == *"="* ]] || continue
  pairs+=("$line")
done < "$ENV_FILE"
[ "${#pairs[@]}" -gt 0 ] || { echo "secrets: no KEY=VALUE lines in $ENV_FILE" >&2; exit 1; }

echo "secrets: setting ${#pairs[@]} secret(s) on '$APP' from $ENV_FILE" >&2
exec flyctl secrets set --app "$APP" "${pairs[@]}"
