# Deploying Nubi

Nubi's hosted deployment config lives here, **in the product repo** — there is no
separate ops repo and no version pin: a deploy builds the current working tree.

## What's here

| Path | Purpose |
|---|---|
| `fly.toml` | Canonical Fly config — one file drives both apps (`nubi`, `nubi-dev`); `app` + `worker` process groups, release-migration step, region `jnb` |
| `setup-fly.sh` | `[main\|dev]` — one-time idempotent app creation (in the `nubi-142` Fly org) |
| `secrets.sh` | `[main\|dev]` — push `.env` / `.env.dev` → Fly secrets in one atomic set |
| `deploy.sh` | `[main\|dev]` — build the EE image (`../Dockerfile.ee`) from this tree and roll it out |
| `dev-local.sh` | Run the whole stack as one local container (+ Postgres, MinIO) via `docker-compose.local.yml` |
| `.env.example` | The secrets contract (`[PER-ENV]` marks dev/prod differences) |

## Environments — dev mirrors prod

| Branch | Fly app | Secrets file | URL |
|---|---|---|---|
| `dev` | `nubi-dev` | `.env.dev` | dev.nubi.sh |
| `main` | `nubi` | `.env` | nubi.sh (production) |

Same config and sizing — only the `--app` name and injected secrets differ.
Validate on `nubi-dev`, then deploy the identical build to `nubi`.

## Infrastructure

- **Compute** — Fly.io, region `jnb`. Two stateless process groups: `app`
  (uvicorn: API + SPA) and `worker` (Flows scheduler + worker pool). Machines
  are disposable; there is **no server-side query/warehouse pool**.
- **Postgres** — **Neon** (`DATABASE_URL`). Use a separate Neon branch for dev.
- **Object storage** — **Tigris** (S3-compatible, `S3_*` + `FLOWS_MATERIALIZE_BASE_URI`
  + `ARTIFACTS_BASE_URI`). Holds materialized/pre-agg extracts, embed snapshots,
  flow artifacts, and uploaded assets — **not** a data warehouse.

## Deploy

```bash
fly auth login

cp deploy/.env.example deploy/.env.dev      # fill: Neon URL, Tigris creds, JWT/connector secrets, URLs
deploy/setup-fly.sh dev                      # create nubi-dev (once)
deploy/secrets.sh   dev                      # push secrets → nubi-dev
deploy/deploy.sh    dev                      # build EE image → roll out (migrate.py --ee on release)

deploy/deploy.sh                             # promote the identical build to prod (nubi)
```

## How billing turns on

Setting `NUBI_LICENSE_KEY` activates the (open) EE code at runtime: `load_ee()`
mounts the billing routers and `GET /features` reports billing on. Absent that
key the same image is just OSS Nubi. EE migrations (`database/migrations/ee/`)
apply automatically on deploy via the `release_command` in `fly.toml`.
