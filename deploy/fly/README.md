# Self-hosting Nubi on Fly.io

A reference deployment of the combined Nubi image (FastAPI API + built SPA +
flows worker) on [Fly.io](https://fly.io). Fly is one option; the same image
runs anywhere a container does (Docker Compose, Cloud Run, Kubernetes…), see
[`docs/self-host.md`](../../docs/self-host.md).

| File | Purpose |
|---|---|
| `fly.toml` | `app` + `worker` process groups, release-time migrations, health check, sizing |
| `setup-fly.sh` | One-time idempotent app creation |
| `secrets.sh <env-file>` | Push an env file to Fly secrets in one atomic set |
| `deploy.sh` | Build `Dockerfile.ee` from your working tree and roll it out |
| `.env.fly.example` | The secrets contract |

You bring: a Postgres database (Neon, Supabase, Fly Postgres, RDS…) and an
S3-compatible bucket (Tigris via `fly storage create`, R2, S3, MinIO) for flow
outputs and uploaded assets. Fly machines are stateless.

```bash
fly auth login
export NUBI_FLY_APP=my-nubi            # pick a globally unique Fly app name
export NUBI_FLY_ORG=personal           # your Fly org slug

cp deploy/fly/.env.fly.example .env.fly && $EDITOR .env.fly
deploy/fly/setup-fly.sh                # create the app (once)
deploy/fly/secrets.sh .env.fly         # push secrets
deploy/fly/deploy.sh                   # build + deploy (migrations run on release)
```

Edit `primary_region` in `fly.toml` to the region nearest your database. Scale
the worker with `fly scale count worker=N --app $NUBI_FLY_APP`; workers lease
tasks so replicas don't collide.

Multiple environments (staging + prod) are just multiple Fly apps: one env file
each, same `fly.toml`, `NUBI_FLY_APP` selects the target.

## Moved from the repo root

Until November 2026 these scripts lived at the repo root and were Nubi Cloud's
own deploy path, with the environment picked by a positional `main|dev`
argument. They now live here, are generic, and pick the target from
`NUBI_FLY_APP`. If you deployed with the old commands:

| Before | Now |
|---|---|
| `./setup-fly.sh dev` | `NUBI_FLY_APP=<app> NUBI_FLY_ORG=<org> deploy/fly/setup-fly.sh` |
| `./secrets.sh dev` (read `.env.dev`) | `NUBI_FLY_APP=<app> deploy/fly/secrets.sh .env.dev` |
| `./deploy.sh dev` | `NUBI_FLY_APP=<app> deploy/fly/deploy.sh` |
| `./deploy.sh` (prod) | not a Fly deploy any more, see below |
| `cp .env.deploy.example .env.dev` | `cp deploy/fly/.env.fly.example .env.fly` |
| `fly.toml` at the root | `deploy/fly/fly.toml` (no `app =` line; `--app` is required) |

An existing `.env` / `.env.dev` keeps working: pass it as the file argument to
`secrets.sh`. Local development (`docker compose`, `dev-local.sh`) is unchanged.

Nubi Cloud itself no longer deploys from this repo at all. CI publishes an image
on every push to `dev` and `main` and on `vX.Y.Z` tags, and a private ops repo
rolls those images out to Cloud Run; see [`DEPLOY.md`](../../DEPLOY.md).
