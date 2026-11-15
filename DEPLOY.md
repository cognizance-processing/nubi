# Deploying Nubi

Nubi ships as one container image: the FastAPI backend with the built SPA
embedded, plus a `worker` entrypoint for flows. Run it anywhere containers run.

| Target | Where to look |
|---|---|
| Docker Compose (simplest self-host) | [`docs/self-host.md`](docs/self-host.md), `docker-compose.yml` |
| Whole stack locally in one container | `dev-local.sh`, `docker-compose.local.yml` |
| Fly.io | [`deploy/fly/`](deploy/fly/README.md) |
| Any other platform (Cloud Run, Kubernetes, …) | Build `Dockerfile` (OSS) or `Dockerfile.ee`; run `python /app/database/migrate.py` (add `--ee` for `Dockerfile.ee`) before each rollout, serve `uvicorn main:app` on 8000, and run `python worker.py` as a separate always-on process |

Published images: `ghcr.io/cognizance-processing/nubi` (OSS) and
`ghcr.io/cognizance-processing/nubi-ee` (includes the inert billing layer), built
by [`.github/workflows/build-image.yml`](.github/workflows/build-image.yml) on
every push to `dev`/`main` and on `vX.Y.Z` tags.

Nubi Cloud (the hosted service) deploys these same public images from a private
ops repository; nothing about that deployment is needed to self-host.
