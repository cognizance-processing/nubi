#!/usr/bin/env bash
# Bring up the Alison Burgers point-of-sale simulator and print the connection
# details Nubi needs. Safe to re-run: it only seeds when the database is empty.
#
#   ./up.sh              start (seed on first run)
#   ./up.sh --reseed     rebuild the dataset from scratch
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER=alison-burgers-sim
IMAGE=mysql:8.0
PORT=3307
ROOT_PW=localdevonly
APP_USER=nubi_reader
APP_PW=BurgerMe2026
DB=alison_pos
PYTHON="${PYTHON:-/home/exo/Documents/nubi/.venv/bin/python}"

RESEED=0
[[ "${1:-}" == "--reseed" ]] && RESEED=1

mysql_root() { docker exec -i "$CONTAINER" mysql -uroot -p"$ROOT_PW" "$@" 2>/dev/null; }

# --- container ------------------------------------------------------------
if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "==> creating $CONTAINER"
  docker run -d --name "$CONTAINER" --restart=unless-stopped \
    -e MYSQL_ROOT_PASSWORD="$ROOT_PW" -p "${PORT}:3306" "$IMAGE" \
    --local-infile=1 --innodb-flush-log-at-trx-commit=2 \
    --max_allowed_packet=256M --innodb-buffer-pool-size=1G >/dev/null
elif ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "==> starting $CONTAINER"
  docker start "$CONTAINER" >/dev/null
fi

printf '==> waiting for MySQL'
for _ in $(seq 1 90); do
  if docker exec "$CONTAINER" mysqladmin ping -uroot -p"$ROOT_PW" --silent >/dev/null 2>&1; then
    echo " ready"; break
  fi
  printf '.'; sleep 1
done

# --- data -----------------------------------------------------------------
ORDERS=$(mysql_root -N -B -e "SELECT COUNT(*) FROM ${DB}.orders" || echo 0)
if [[ "$RESEED" == "1" || "${ORDERS:-0}" -lt 1 ]]; then
  echo "==> seeding (about two and a half minutes)"
  "$PYTHON" "$HERE/seed.py" --port "$PORT" --password "$ROOT_PW"
  ORDERS=$(mysql_root -N -B -e "SELECT COUNT(*) FROM ${DB}.orders")
fi

# --- a read-only login, which is what a customer would actually hand over ---
mysql_root -e "
  CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '${APP_PW}';
  ALTER USER '${APP_USER}'@'%' IDENTIFIED WITH mysql_native_password BY '${APP_PW}';
  GRANT SELECT ON ${DB}.* TO '${APP_USER}'@'%';
  FLUSH PRIVILEGES;"

FIRST=$(mysql_root -N -B -e "SELECT MIN(order_date) FROM ${DB}.orders")
LAST=$(mysql_root -N -B -e "SELECT MAX(order_date) FROM ${DB}.orders")
ITEMS=$(mysql_root -N -B -e "SELECT COUNT(*) FROM ${DB}.order_items")

commafy() { echo "$1" | sed -e :a -e 's/\(.*[0-9]\)\([0-9]\{3\}\)/\1,\2/;ta'; }
ORDERS_F=$(commafy "$ORDERS"); ITEMS_F=$(commafy "$ITEMS")

bold=$(tput bold 2>/dev/null || true); dim=$(tput dim 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)

cat <<BANNER

${bold}  ALISON BURGERS — point-of-sale replica${reset}
  ${dim}${ORDERS_F} orders · ${ITEMS_F} line items · ${FIRST} to ${LAST}${reset}

  ${bold}Connect Nubi with these details${reset}

      Connector type   MySQL
      Host             127.0.0.1
      Port             ${PORT}
      Database         ${DB}
      Username         ${APP_USER}
      Password         ${APP_PW}

  ${dim}Read-only login. Tables: stores, menu_items, staff, shifts, orders,
  order_items, reviews.  Stop with ./down.sh${reset}

BANNER
