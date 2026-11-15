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

## How Nubi Cloud ships (for contributors)

Nothing in this repo deploys anything. The workflow only publishes images, and
the private ops repo decides what runs where:

| Git event | Image published | What happens next |
|---|---|---|
| push to `dev` | `nubi-ee:dev-<sha>` | ops repo deploys it to the **dev** environment automatically |
| push to `main` | `nubi-ee:main-<sha>` | published only; **merging to `main` does not ship to prod** |
| tag `vX.Y.Z` | `nubi-ee:X.Y.Z`, `nubi-ee:latest`, `nubi:X.Y.Z`, `nubi:latest` | ops repo stages a **prod** rollout that waits for a reviewer's approval |
| tag `vX.Y.Z-rc.N` | `nubi-ee:X.Y.Z-rc.N` | published only, no auto-deploy (a QA environment is planned) |

So a production release is: merge to `main`, bump `version` in `package.json`,
tag `vX.Y.Z` on that commit, push the tag, then approve the rollout in the ops
repo. The digest that ran on dev is the digest that reaches prod; nothing is
rebuilt downstream. Before this (Nov 2026) `./deploy.sh` at the repo root
built the working tree and pushed it straight to Fly; that path is gone, see
[`deploy/fly/README.md`](deploy/fly/README.md#moved-from-the-repo-root).
