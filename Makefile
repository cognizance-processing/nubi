.PHONY: up down logs migrate smoke config-check

# ── Stack lifecycle ───────────────────────────────────────────────────────────

## up — Build images (if needed) and start the full stack in the background.
##       First run can take a few minutes while npm ci + pip install run.
##       Re-run `make up` after code changes to rebuild and restart.
up:
	docker compose up -d --build

## down — Stop all services and remove volumes (wipes the database).
##         Omit `-v` if you want to keep the Postgres data volume.
down:
	docker compose down -v

## logs — Stream logs from all services.  Ctrl-C to stop.
logs:
	docker compose logs -f

# ── Database ──────────────────────────────────────────────────────────────────

## migrate — Apply any pending SQL migrations inside the running backend container.
##           Runs automatically on startup via docker-entrypoint.sh; use this to
##           apply migrations without restarting the container.
migrate:
	docker compose exec backend python /app/database/migrate.py

## migrate-status — Show which migrations have been applied and which are pending.
migrate-status:
	docker compose exec backend python /app/database/migrate.py --status

# ── Testing ───────────────────────────────────────────────────────────────────

## smoke — Run the end-to-end smoke test against the running stack.
##          Starts the stack if it is not already up, waits for health, runs
##          the test, then tears down.  Requires curl and jq.
smoke:
	bash scripts/smoke.sh

# ── Validation ────────────────────────────────────────────────────────────────

## config-check — Validate the docker-compose.yml syntax (requires Docker).
config-check:
	docker compose config --quiet && echo "docker-compose.yml: OK"
