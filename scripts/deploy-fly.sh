#!/usr/bin/env bash
# deploy-fly.sh — deploy Nubi to Fly.io (combined image, app + worker).
#
# Idempotent: safe to re-run. First run creates the Fly app; every run builds
# the combined image remotely and rolls it out. Migrations run automatically
# via the release_command in fly.toml (database/migrate.py).
#
# Usage:
#   scripts/deploy-fly.sh                 # deploy
#   scripts/deploy-fly.sh --secrets-only  # print the secrets checklist and exit
#
# Prereqs: flyctl (https://fly.io/docs/flyctl/install/), `fly auth login`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

APP_NAME="nubi"
REGION="jnb"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if ! command -v flyctl >/dev/null 2>&1; then
    echo "ERROR: flyctl is not installed." >&2
    echo "  brew install flyctl   # or: curl -L https://fly.io/install.sh | sh" >&2
    exit 1
fi

if ! flyctl auth whoami >/dev/null 2>&1; then
    echo "ERROR: not authenticated with Fly.io. Run: fly auth login" >&2
    exit 1
fi

if [ ! -f fly.toml ]; then
    echo "ERROR: fly.toml not found at repo root ($REPO_ROOT)." >&2
    exit 1
fi

# ── Secrets checklist (derived from backend/app/config.py) ───────────────────
print_secrets_help() {
    cat <<'EOF'
────────────────────────────────────────────────────────────────────────────────
Secrets — set with:  fly secrets set -a nubi KEY=value [KEY=value ...]
(Setting secrets on a running app triggers a restart; on first run, set them
all BEFORE the first deploy so the release migration can reach the database.)

REQUIRED (the backend refuses to start without these):
  DATABASE_URL            Neon Postgres URL, e.g.
                          postgresql://user:pass@ep-xxx.aws.neon.tech/nubi?sslmode=require
  JWT_SECRET              >= 32 bytes. Generate:
                          python -c "import secrets; print(secrets.token_hex(32))"
  GOOGLE_CLIENT_ID        Google OAuth client (login)
  GOOGLE_CLIENT_SECRET    Google OAuth client secret
  GOOGLE_REDIRECT_URI     e.g. https://nubi.fly.dev/api/v1/auth/google/callback
  FRONTEND_URL            e.g. https://nubi.fly.dev (SPA is same-origin)

REQUIRED IN PRACTICE (defaults exist but are unsafe/non-functional in prod):
  SUPERUSER_EMAIL         bootstrap admin account (seed flow)
  SUPERUSER_PASSWORD      override the insecure default!
  SUPERUSER_NAME          display name for the bootstrap admin
  CONNECTOR_SECRET_KEY    base64 32-byte AES key for connector credentials:
                          python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"

OBJECT STORAGE — Cloudflare R2 (S3-compatible), for materialized/incremental
flow targets and parquet storage:
  FLOWS_MATERIALIZE_BASE_URI  e.g. s3://nubi-flows/flows
  S3_ENDPOINT_URL             https://<account-id>.r2.cloudflarestorage.com
  S3_ACCESS_KEY               R2 access key id
  S3_SECRET_KEY               R2 secret access key
  AWS_ACCESS_KEY_ID           (same value as S3_ACCESS_KEY — some code paths
  AWS_SECRET_ACCESS_KEY        read the AWS_* names)
  AWS_REGION                  "auto" for R2

OPTIONAL — set what you use:
  ANTHROPIC_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY   AI features (LLM_PROVIDER picks)
  FLOWS_TICK_SECRET        only if you use the POST /flows/tick webhook instead
                           of (or alongside) the always-on worker process
  KERNEL_REMOTE_PROVIDER + E2B_API_KEY (or MODAL_TOKEN_ID/MODAL_TOKEN_SECRET)
                           sandboxed notebook kernel; also set
                           KERNEL_LOCAL_ENABLED=false in production
  SLACK_BOT_TOKEN / SLACK_ALERT_WEBHOOK / SLACK_ALERT_CHANNEL / SLACK_SIGNING_SECRET
  WHATSAPP_SEND_TOKEN / WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ALERT_RECIPIENT / WHATSAPP_APP_SECRET
  SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM   invoice + report email
  ALERT_EMAIL_RECIPIENT    flow-run alert emails
  IPINFO_TOKEN             login-event geolocation
  GIT_REMOTE_PROVIDER      github_app | gitlab — env-as-branch git pushes
    GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY / GITHUB_APP_INSTALLATION_ID
    GITLAB_TOKEN / GITLAB_HOST
  CHAT_DEFAULT_ORG_ID / CHAT_ORG_BINDINGS   chat-over-channel org binding
  CORS_ORIGINS             extra browser origins (same-origin SPA needs none)

Example first-run bootstrap:
  fly secrets set -a nubi \
    DATABASE_URL='postgresql://...?sslmode=require' \
    JWT_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
    GOOGLE_CLIENT_ID='...' GOOGLE_CLIENT_SECRET='...' \
    GOOGLE_REDIRECT_URI='https://nubi.fly.dev/api/v1/auth/google/callback' \
    FRONTEND_URL='https://nubi.fly.dev' \
    SUPERUSER_EMAIL='you@example.com' SUPERUSER_PASSWORD='...' SUPERUSER_NAME='You' \
    CONNECTOR_SECRET_KEY="$(python -c 'import os,base64; print(base64.b64encode(os.urandom(32)).decode())')"
────────────────────────────────────────────────────────────────────────────────
EOF
}

if [ "${1:-}" = "--secrets-only" ]; then
    print_secrets_help
    exit 0
fi

# ── App creation (first run only) ─────────────────────────────────────────────
if flyctl status -a "$APP_NAME" >/dev/null 2>&1; then
    echo "==> Fly app '$APP_NAME' exists."
else
    echo "==> Fly app '$APP_NAME' not found — creating it (region: $REGION)."
    echo "    (No Fly Postgres is provisioned: the database is Neon, object"
    echo "     storage is Cloudflare R2 — both external, configured via secrets.)"
    flyctl apps create "$APP_NAME"
fi

# ── Required secrets present? ─────────────────────────────────────────────────
REQUIRED_SECRETS=(DATABASE_URL JWT_SECRET GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI FRONTEND_URL)
EXISTING_SECRETS="$(flyctl secrets list -a "$APP_NAME" 2>/dev/null || true)"
MISSING=()
for s in "${REQUIRED_SECRETS[@]}"; do
    if ! printf '%s\n' "$EXISTING_SECRETS" | grep -q "^${s}[[:space:]]"; then
        MISSING+=("$s")
    fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
    echo ""
    echo "==> Missing required secrets: ${MISSING[*]}"
    print_secrets_help
    echo "Set the missing secrets above, then re-run this script." >&2
    exit 1
fi

# ── Deploy ────────────────────────────────────────────────────────────────────
echo "==> Deploying '$APP_NAME' (remote build; migrations run via release_command)..."
flyctl deploy --remote-only -a "$APP_NAME"

echo ""
echo "==> Done. Useful follow-ups:"
echo "    fly status -a $APP_NAME"
echo "    fly logs -a $APP_NAME"
echo "    fly scale count app=1 worker=1 -a $APP_NAME -y   # pin machine counts"
echo "    (worker is always-on; scale it later with fly-autoscaler on task_runs depth)"
