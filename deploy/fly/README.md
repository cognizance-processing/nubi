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
