"""Tests for the onboarding *sample bundle* seeder (``app.sample``).

Coverage
--------
1.  ``seed_sample_bundle`` creates the full bundle (datastore + 4 queries +
    1 board), all tagged ``config.sample=true`` and scoped to the project.
2.  Seeding is idempotent — a second call creates nothing new.
3.  The Sample datastore points at the bundled, absolute ``sample.duckdb`` path
    with ``type=duckdb`` so the query route opens it read-only.
4.  The bundled DuckDB file is a real star schema that joins on every fact row.
5.  ``remove_sample_bundle`` deletes every sample resource and is idempotent.
6.  Removing then restoring round-trips the bundle.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.repos.memory import InMemoryRepo
from app.sample import remove_sample_bundle, seed_sample_bundle


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


@pytest.mark.asyncio
async def test_seed_creates_full_bundle_tagged_sample() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()

    summary = await seed_sample_bundle(org, project, user, repo)

    assert "skipped" not in summary, summary
    datastores = await repo.list("datastores", org)
    queries = await repo.list("queries", org)
    boards = await repo.list("boards", org)

    assert len(datastores) == 1
    assert len(queries) == 4
    assert len(boards) == 1

    # Every created resource is tagged sample=true and scoped to the project.
    for row in (*datastores, *queries, *boards):
        assert row["config"].get("sample") is True
        assert row["config"].get("sample_id")
        assert str(row["project_id"]) == project


@pytest.mark.asyncio
async def test_seed_is_idempotent() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()

    await seed_sample_bundle(org, project, user, repo)
    second = await seed_sample_bundle(org, project, user, repo)

    assert second["created"] == []  # nothing new created on re-run
    assert len(await repo.list("datastores", org)) == 1
    assert len(await repo.list("queries", org)) == 4
    assert len(await repo.list("boards", org)) == 1


@pytest.mark.asyncio
async def test_datastore_points_at_bundled_file() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)

    ds = (await repo.list("datastores", org))[0]
    cfg = ds["config"]
    assert cfg["type"] == "duckdb"
    assert os.path.isabs(cfg["database"])
    assert cfg["database"].endswith("sample.duckdb")
    assert os.path.exists(cfg["database"])  # built lazily by the seeder


@pytest.mark.asyncio
async def test_bundled_file_is_a_real_joinable_star_schema() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)
    db_path = (await repo.list("datastores", org))[0]["config"]["database"]

    import duckdb

    con = duckdb.connect(database=db_path, read_only=True)
    try:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        assert {"sales", "dim_regions", "dim_products", "dim_customers", "budget", "targets"} <= tables

        total = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        joined = con.execute(
            """
            SELECT COUNT(*) FROM sales s
            JOIN dim_regions   r ON s.region_id   = r.region_id
            JOIN dim_products  p ON s.product_id  = p.product_id
            JOIN dim_customers c ON s.customer_id = c.customer_id
            """
        ).fetchone()[0]
        assert total > 0
        assert joined == total  # every fact row resolves all three dims
    finally:
        con.close()


@pytest.mark.asyncio
async def test_remove_then_restore_round_trips() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)

    removed = await remove_sample_bundle(org, project, repo)
    assert removed == {"boards": 1, "queries": 4, "datastores": 1}
    assert await repo.list("datastores", org) == []
    assert await repo.list("queries", org) == []
    assert await repo.list("boards", org) == []

    # Removing again is a no-op (idempotent).
    assert await remove_sample_bundle(org, project, repo) == {
        "boards": 0,
        "queries": 0,
        "datastores": 0,
    }

    # Restore re-creates the whole bundle.
    await seed_sample_bundle(org, project, user, repo)
    assert len(await repo.list("datastores", org)) == 1
    assert len(await repo.list("queries", org)) == 4
    assert len(await repo.list("boards", org)) == 1
