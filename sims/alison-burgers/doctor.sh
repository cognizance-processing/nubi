#!/usr/bin/env bash
# Why is there no data?
#
# Walks the chain from Docker to a real board query and stops being vague: each
# line says what it checked, and a failure says the command that fixes it.
# Read it top-down — the first ✗ is the cause; everything below it is a symptom.
#
#   ./doctor.sh                 checks Docker → MySQL → data → Nubi's API
#   NUBI_TOKEN=nubi_ak_… ./doctor.sh   also asks Nubi to query the sim for real
set -uo pipefail

CONTAINER=alison-burgers-sim
PORT=3307
DB=alison_pos
ROOT_PW=localdevonly
APP_USER=nubi_reader
APP_PW=BurgerMe2026
API=${NUBI_API:-http://localhost:8000/api/v1}
WEB=${NUBI_WEB:-http://localhost:5173}

fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n     \033[2m→ %s\033[0m\n' "$1" "$2"; fail=1; }
warn() { printf '  \033[33m!\033[0m %s\n     \033[2m→ %s\033[0m\n' "$1" "$2"; }
skip() { printf '  \033[2m- skipped: %s\033[0m\n' "$1"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

mysql_root() { docker exec -i "$CONTAINER" mysql -uroot -p"$ROOT_PW" -N -B "$@" 2>/dev/null; }

# ── 1. the sim ────────────────────────────────────────────────────────────
head_ "The simulator"

if ! docker info >/dev/null 2>&1; then
  bad "Docker is not responding" "start Docker, then re-run this"
  echo; echo "Nothing below can pass until Docker is up."; exit 1
fi
ok "Docker is running"

if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  bad "no container named $CONTAINER" "./up.sh   (first run seeds; takes ~3 min)"
elif ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  bad "$CONTAINER exists but is stopped" "./up.sh   (~3s, data is still there)"
else
  ok "$CONTAINER is running ($(docker ps --format '{{.Status}}' -f name=$CONTAINER))"
fi

if [ "$fail" -eq 0 ]; then
  if docker exec "$CONTAINER" mysqladmin ping -uroot -p"$ROOT_PW" --silent >/dev/null 2>&1; then
    ok "MySQL is accepting connections inside the container"
  else
    bad "MySQL is not answering yet" "wait ~15s and re-run — it is still starting"
  fi
fi

# ── 2. the data ───────────────────────────────────────────────────────────
head_ "The data"

if [ "$fail" -ne 0 ]; then
  skip "the simulator has to be up before its data can be read"
else
  orders=$(mysql_root -e "SELECT COUNT(*) FROM ${DB}.orders" || echo "")
  stores=$(mysql_root -e "SELECT COUNT(*) FROM ${DB}.stores" || echo "")
  if [ -z "$orders" ]; then
    bad "database ${DB} is missing or unreadable" "./up.sh --reseed   (~3 min)"
  elif [ "$orders" -lt 1000 ]; then
    bad "only ${orders} orders — a seed was interrupted" "./up.sh --reseed   (~3 min)"
  else
    ok "$(printf "%'d" "$orders") orders, ${stores} stores"
    span=$(mysql_root -e "SELECT CONCAT(MIN(order_date),' to ',MAX(order_date)) FROM ${DB}.orders")
    ok "covering ${span}"
    [ "${stores:-0}" -eq 18 ] || warn "expected 18 stores, found ${stores}" \
      "the maps need all 9 provinces — ./up.sh --reseed"
  fi
fi

# ── 3. the login Nubi actually uses ───────────────────────────────────────
head_ "The connection Nubi uses"

if [ "$fail" -ne 0 ]; then
  skip "fix the simulator first"
else
  # From OUTSIDE the container, on the published port — the same path the
  # backend takes. An in-container check would pass while this one fails.
  probe=$(docker run --rm --network=host mysql:8.0 \
            mysql -h 127.0.0.1 -P "$PORT" -u"$APP_USER" -p"$APP_PW" -N -B \
            -e "SELECT COUNT(*) FROM ${DB}.stores" 2>&1 | tail -1)
  if [[ "$probe" =~ ^[0-9]+$ ]]; then
    ok "${APP_USER}@127.0.0.1:${PORT} can read ${DB} (${probe} stores)"
  else
    bad "${APP_USER} cannot connect on 127.0.0.1:${PORT}" \
        "./up.sh   (it recreates the read-only login and the port mapping)"
    printf '     \033[2m%s\033[0m\n' "${probe:0:110}"
  fi
fi

# ── 4. the app ────────────────────────────────────────────────────────────
head_ "Nubi"

code=$(curl -s -m 4 -o /dev/null -w '%{http_code}' "${API}/health" 2>/dev/null)
case "$code" in
  401|200) ok "backend is up on ${API%/api/v1} (401 here means auth required — that is healthy)" ;;
  000)     bad "backend is not answering" ".venv/bin/python backend/run.py --no-install --no-sims --no-reload" ;;
  *)       warn "backend answered ${code}" "check the terminal it is running in" ;;
esac

# A backend that has been up for hours can answer cheap routes instantly and
# still stall on anything that opens a database connection. Time one real check
# rather than trusting /health.
if [ "$code" = "401" ] || [ "$code" = "200" ]; then
  t0=$(date +%s%N)
  curl -s -m 12 -o /dev/null "${API}/health" 2>/dev/null
  t1=$(date +%s%N)
  ms=$(( (t1 - t0) / 1000000 ))
  if [ "$ms" -gt 2000 ]; then
    warn "backend took ${ms} ms to answer a trivial route" \
         "restart it — a long-lived process can degrade: Ctrl-C and re-run backend/run.py"
  fi
fi

web=$(curl -s -m 4 -o /dev/null -w '%{http_code}' "$WEB" 2>/dev/null)
[ "$web" = "200" ] && ok "frontend is up on ${WEB}" \
  || bad "frontend is not answering" "npm run dev"

agents=$(pgrep -fc '[a]pp.bridges.agent' 2>/dev/null || echo 0)
if [ "${agents:-0}" -gt 0 ]; then
  ok "${agents} bridge agent(s) running"
else
  warn "no bridge agent running" \
    "only matters for the via-bridge connector; see the runbook's pre-flight"
fi

# ── 5. end to end, if a token is available ────────────────────────────────
if [ -n "${NUBI_TOKEN:-}" ]; then
  head_ "End to end"
  # Ask Nubi to test every connector it has. That endpoint opens a real socket
  # and runs a real query — through the bridge tunnel for a bridge connector —
  # and hands back the driver's own words when it fails. A raw /query probe
  # would stream Arrow and tell us far less.
  conns=$(curl -s -m 10 "${API}/connectors" -H "Authorization: Bearer ${NUBI_TOKEN}" 2>/dev/null)
  ids=$(printf '%s' "$conns" | python3 -c "
import json,sys
try: rows = json.load(sys.stdin)
except Exception: sys.exit(0)
rows = rows.get('connectors', rows) if isinstance(rows, dict) else rows
for r in rows:
    if r.get('id') != '__demo__':
        print(r['id'], r.get('name', '?').replace(' ', '\\u00a0'))
" 2>/dev/null)

  if [ -z "$ids" ]; then
    bad "no connectors found for this token" \
        "check the token is for the Alison Burgers workspace (a key is pinned to one org)"
  else
    while read -r cid cname; do
      [ -z "$cid" ] && continue
      res=$(curl -s -m 45 -X POST "${API}/connectors/${cid}/test" \
              -H "Authorization: Bearer ${NUBI_TOKEN}" 2>/dev/null)
      okflag=$(printf '%s' "$res" | python3 -c "import json,sys;d=json.load(sys.stdin);print('1' if d.get('ok') else '0')" 2>/dev/null)
      msg=$(printf '%s' "$res" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('checked',''),(str(d.get('elapsed_ms'))+' ms') if d.get('elapsed_ms') is not None else '')" 2>/dev/null)
      pretty=$(printf '%s' "$cname" | sed 's/\\u00a0/ /g')
      if [ "$okflag" = "1" ]; then
        ok "${pretty} — ${msg}"
      else
        if [ -z "$msg" ]; then
          bad "${pretty} — no response from the backend" \
              "restart the backend; if it then passes, the process had degraded"
        else
          bad "${pretty} — ${msg}" \
              "that message is the database driver's own; fix what it names"
        fi
      fi
    done <<< "$ids"
  fi
else
  head_ "End to end"
  printf '  \033[2m- skipped. For the full check:\033[0m\n'
  printf '  \033[2m    NUBI_TOKEN=nubi_ak_… ./doctor.sh\033[0m\n'
  printf '  \033[2m  Or just open Connectors in the app and press Test.\033[0m\n'
fi

echo
if [ "$fail" -eq 0 ]; then
  printf '\033[32mAll clear.\033[0m If a widget is still empty, it is not the sim —\n'
  printf 'check the widget'"'"'s connector and query in Configure → Data.\n'
else
  printf '\033[31mFix the first ✗ above, then re-run.\033[0m\n'
fi
exit "$fail"
