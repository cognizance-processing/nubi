.PHONY: up down migrate logs smoke

# Start the full stack (build images if needed)
up:
	docker compose up -d --build

# Stop the stack and remove volumes
down:
	docker compose down -v

# Run database migrations inside the running app container
migrate:
	docker compose exec app python /app/database/migrate.py

# Stream logs from all services (Ctrl-C to stop)
logs:
	docker compose logs -f

# Run the end-to-end smoke test against the running stack
smoke:
	bash scripts/smoke.sh
