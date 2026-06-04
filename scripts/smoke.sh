#!/usr/bin/env bash
# scripts/smoke.sh — End-to-end smoke test against the running Nubi stack.
# Usage: bash scripts/smoke.sh
# Requires: curl, jq
set -euo pipefail

BASE="http://localhost:8000"
API="${BASE}/api/v1"

# Unique email per run to avoid conflicts
RAND="${RANDOM}${RANDOM}"
EMAIL="smoke+${RAND}@test.dev"
PASSWORD="smoketest-pw-123"
NAME="Smoke"

echo "============================================"
echo " Nubi M10 Smoke Test"
echo "============================================"
echo " Backend: ${BASE}"
echo " Test email: ${EMAIL}"
echo ""

# ── 1. Health check ───────────────────────────────────────────────────────────
echo "[1/5] GET /health ..."
HEALTH=$(curl -sf "${BASE}/health")
echo "  Response: ${HEALTH}"
STATUS=$(echo "${HEALTH}" | jq -r '.status')
DB_STATUS=$(echo "${HEALTH}" | jq -r '.db')
if [ "${STATUS}" != "ok" ]; then
  echo "  FAIL: expected status=ok, got '${STATUS}'" >&2
  exit 1
fi
if [ "${DB_STATUS}" != "ok" ]; then
  echo "  FAIL: expected db=ok, got '${DB_STATUS}'" >&2
  exit 1
fi
echo "  PASS: /health -> status=ok, db=ok"
echo ""

# ── 2. Register ───────────────────────────────────────────────────────────────
echo "[2/5] POST /auth/register ..."
REG_BODY="{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"name\":\"${NAME}\"}"
REG_RESP=$(curl -sf -X POST "${API}/auth/register" \
  -H "Content-Type: application/json" \
  -d "${REG_BODY}")
echo "  Response keys: $(echo "${REG_RESP}" | jq 'keys')"

ACCESS_TOKEN=$(echo "${REG_RESP}" | jq -r '.access_token // empty')
if [ -z "${ACCESS_TOKEN}" ]; then
  echo "  FAIL: no access_token in register response" >&2
  echo "  Full response: ${REG_RESP}" >&2
  exit 1
fi
echo "  PASS: /auth/register -> access_token obtained (${#ACCESS_TOKEN} chars)"
echo ""

# ── 3. GET /auth/me ───────────────────────────────────────────────────────────
echo "[3/5] GET /auth/me ..."
ME_RESP=$(curl -sf "${API}/auth/me" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}")
echo "  Response: ${ME_RESP}"
ME_EMAIL=$(echo "${ME_RESP}" | jq -r '.user.email // empty')
if [ -z "${ME_EMAIL}" ]; then
  echo "  FAIL: email not present in /auth/me response" >&2
  exit 1
fi
if [ "${ME_EMAIL}" != "${EMAIL}" ]; then
  echo "  FAIL: expected email '${EMAIL}', got '${ME_EMAIL}'" >&2
  exit 1
fi
echo "  PASS: /auth/me -> email=${ME_EMAIL}"
echo ""

# ── 4. POST /query {query_id: demo_all} ──────────────────────────────────────
echo "[4/5] POST /query {query_id: demo_all} ..."
QUERY_RESP=$(curl -sf -X POST "${API}/query" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query_id":"demo_all"}' \
  -o /tmp/nubi_demo_all.arrow \
  -w "%{http_code}")
echo "  HTTP status: ${QUERY_RESP}"
if [ "${QUERY_RESP}" != "200" ]; then
  echo "  FAIL: expected 200, got ${QUERY_RESP}" >&2
  exit 1
fi
BODY_LEN=$(wc -c < /tmp/nubi_demo_all.arrow)
echo "  Response body: ${BODY_LEN} bytes (Arrow IPC)"
if [ "${BODY_LEN}" -lt 1 ]; then
  echo "  FAIL: empty body from /query demo_all" >&2
  exit 1
fi
echo "  PASS: /query demo_all -> 200, ${BODY_LEN} bytes Arrow IPC"
echo ""

# ── 5. POST /query {query_id: demo_points_10k} ───────────────────────────────
echo "[5/5] POST /query {query_id: demo_points_10k} ..."
QUERY_RESP2=$(curl -sf -X POST "${API}/query" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query_id":"demo_points_10k"}' \
  -o /tmp/nubi_demo_points_10k.arrow \
  -w "%{http_code}")
echo "  HTTP status: ${QUERY_RESP2}"
if [ "${QUERY_RESP2}" != "200" ]; then
  echo "  FAIL: expected 200, got ${QUERY_RESP2}" >&2
  exit 1
fi
BODY_LEN2=$(wc -c < /tmp/nubi_demo_points_10k.arrow)
echo "  Response body: ${BODY_LEN2} bytes (Arrow IPC)"
# 10k rows of 4 columns (int64 + float64 x3) should be well over 300 KB
if [ "${BODY_LEN2}" -lt 10000 ]; then
  echo "  FAIL: body too small for 10k rows (got ${BODY_LEN2} bytes)" >&2
  exit 1
fi
echo "  PASS: /query demo_points_10k -> 200, ${BODY_LEN2} bytes Arrow IPC (~10k rows)"
echo ""

echo "============================================"
echo " ALL 5 SMOKE TESTS PASSED"
echo "============================================"
