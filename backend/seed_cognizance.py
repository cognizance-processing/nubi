"""Seed the migrated COGNIZANCE workspace into $DATABASE_URL (idempotent).

Replaces the demo seed. Creates:
  - the owner user + personal org (same superuser as seed.py / seed_demo.py)
  - 1 BigQuery datastore (project cog-analytics-etl-pipeline), with the scoped
    read-only service-account JSON loaded from migration_cognizance/keyfile.scoped.json
    (if present) into the encrypted secret store
  - the migrated queries (legacy Go-template SQL rendered to concrete BigQuery SQL)
  - the 10 migrated boards (DashboardSpec with filters/drilldown drawers)

Source of truth: migration_cognizance/migration_artifact.json (produced by
legacy_to_spec.py). All objects are keyed by config.seed_id for idempotency.

Usage
-----
    cd backend && DATABASE_URL=postgresql://... python seed_cognizance.py
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import close_db, execute, fetchrow, init_db
from app.routes.auth import _create_personal_org
from seed_demo import _upsert_resource  # reuse the idempotent upsert helper

_s = get_settings()
TEST_EMAIL = _s.SUPERUSER_EMAIL
TEST_PASSWORD = _s.SUPERUSER_PASSWORD
TEST_NAME = _s.SUPERUSER_NAME

ARTIFACT = Path(__file__).parent / "migration_cognizance" / "migration_artifact.json"
KEYFILE = Path(__file__).parent / "migration_cognizance" / "keyfile.scoped.json"

SEED_DS = "cognizance:datastore:bigquery"


async def seed_cognizance() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    await init_db()
    try:
        # ── 1. Owner user + org ──
        existing_user = await fetchrow("SELECT id FROM users WHERE email = $1", TEST_EMAIL)
        user_created = False
        if existing_user is None:
            user_id = str(uuid.uuid4())
            await execute(
                "INSERT INTO users (id, email, password_hash, name, email_verified)"
                " VALUES ($1, $2, $3, $4, true)",
                user_id, TEST_EMAIL, hash_password(TEST_PASSWORD), TEST_NAME,
            )
            await _create_personal_org(user_id, TEST_NAME, TEST_EMAIL)
            user_created = True
        else:
            user_id = str(existing_user["id"])

        org_row = await fetchrow(
            "SELECT org_id FROM org_members WHERE user_id = $1::uuid ORDER BY org_id LIMIT 1",
            user_id,
        )
        assert org_row is not None, "User has no org membership."
        org_id = str(org_row["org_id"])

        # ── 2. BigQuery datastore (+ scoped secret) ──
        ds = artifact["datastore"]
        ds_cfg = {
            "type": "bigquery",
            "project": ds["config"]["projectId"],
            "project_id": ds["config"]["projectId"],
            "description": ds["config"].get("description", ""),
        }
        datastore, ds_created = await _upsert_resource(
            "datastores", SEED_DS, org_id, user_id, ds["name"], ds_cfg,
        )
        ds_id = str(datastore["id"])

        # Load the scoped service-account JSON into the encrypted secret store.
        secret_status = "absent"
        if KEYFILE.exists():
            try:
                from app.connectors.secret_store import get_secret_store
                sa_json = KEYFILE.read_text()
                json.loads(sa_json)  # validate it parses
                await get_secret_store().put(ds_id, org_id, {"service_account_json": sa_json})
                secret_status = "loaded"
            except Exception as exc:  # noqa: BLE001
                secret_status = f"FAILED: {exc}"

        # ── 3. Queries (legacy_id -> new row id) ──
        qid_map: dict[str, str] = {}
        q_created = 0
        for q in artifact["queries"]:
            seed_id = f"cognizance:query:{q['legacy_id']}"
            row, created = await _upsert_resource(
                "queries", seed_id, org_id, user_id, q["name"][:200] or "query",
                {"sql": q["sql"], "datastore_id": ds_id, "params": []},
            )
            qid_map[q["legacy_id"]] = str(row["id"])
            q_created += int(created)

        # ── 4. Boards (rewrite widget query ids legacy -> new) ──
        def remap(spec: dict) -> dict:
            for w in spec.get("widgets", []):
                for key in ("query_id", "options_query_id"):
                    legacy = w.get(key)
                    if legacy and legacy in qid_map:
                        w[key] = qid_map[legacy]
                    elif legacy and key == "query_id":
                        w[key] = ""  # query not migrated -> renders empty
                    elif legacy and key == "options_query_id":
                        w[key] = None
            return spec

        boards_created = 0
        board_names = []
        for b in artifact["boards"]:
            seed_id = f"cognizance:board:{b['legacy_id']}"
            spec = remap(b["spec"])
            row, created = await _upsert_resource(
                "boards", seed_id, org_id, user_id, b["name"][:200], {"spec": spec},
            )
            boards_created += int(created)
            board_names.append((b["name"], created))

        # ── Summary ──
        print("\n" + "=" * 64)
        print("  Nubi — COGNIZANCE migration seed")
        print("=" * 64)
        print(f"  User        [{'CREATED' if user_created else 'exists '}]  {TEST_EMAIL}")
        print(f"  Org ID                   {org_id}")
        print(f"  Datastore   [{'CREATED' if ds_created else 'exists '}]  {ds['name']}  (BigQuery)")
        print(f"  SA secret   [{secret_status}]")
        print(f"  Queries     {q_created} created / {len(artifact['queries'])} total")
        print(f"  Boards      {boards_created} created / {len(artifact['boards'])} total")
        for name, created in board_names:
            print(f"     [{'NEW' if created else ' · '}] {name}")
        if secret_status != "loaded":
            print("\n  ⚠ No BigQuery credentials loaded. Generate the scoped keyfile:")
            print("      bash backend/migration_cognizance/make_scoped_keyfile.sh")
            print("    then re-run this seed to enable live data.")
        print(f"\n  Login:  {TEST_EMAIL} / {TEST_PASSWORD}")
        print("=" * 64 + "\n")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(seed_cognizance())
