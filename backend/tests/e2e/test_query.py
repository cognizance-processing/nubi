"""E2E: Query endpoint tests.

- Registered query_id → 200 Arrow with rows > 0
- Raw SQL (author token) → rows
- read-only token + raw SQL → 403 insufficient_scope
- Embed-scoped token + raw SQL → 403 (no author:sql)
"""

from __future__ import annotations

import pytest


@pytest.mark.usefixtures("e2e_ctx")
class TestQuery:
    def test_raw_sql_author_token(self, e2e_ctx):
        """Superuser (has author:sql implicitly) can execute raw SQL."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT region, ROUND(SUM(nsv), 2) AS nsv FROM sales GROUP BY region ORDER BY nsv DESC"},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        from tests.e2e.conftest import read_arrow_bytes
        rows = read_arrow_bytes(resp.content)
        assert len(rows) >= 1
        assert "region" in rows[0]
        assert "nsv" in rows[0]
        # Gauteng is the largest region
        assert rows[0]["region"] == "Gauteng"

    def test_raw_sql_read_only_scope_403(self, e2e_ctx):
        """read:* token without author:sql cannot run raw SQL → 403."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT 1 AS x"},
            headers={
                **e2e_ctx.read_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        # read:* token does not carry author:sql, so raw SQL → 403
        assert resp.status_code == 403, (
            f"Expected 403 for read-only token on raw SQL, got {resp.status_code}: {resp.text}"
        )

    def test_raw_sql_embed_scope_403(self, e2e_ctx):
        """Token with only read:query scope cannot run raw SQL → 403."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT 1 AS x"},
            headers={
                **e2e_ctx.embed_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 403, (
            f"Expected 403 for embed-scoped token on raw SQL, got {resp.status_code}: {resp.text}"
        )

    def test_registered_query_id(self, e2e_ctx):
        """A registered query_id resolves and returns real Arrow data."""
        # Use a query we know exists (retail region x month)
        q_ids = e2e_ctx.query_ids
        if not q_ids:
            pytest.skip("No non-metric queries found in DB")
        query_id = next(iter(q_ids.values()))
        resp = e2e_ctx.client.post(
            "/query",
            json={"query_id": query_id},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        from tests.e2e.conftest import read_arrow_bytes
        rows = read_arrow_bytes(resp.content)
        assert len(rows) > 0

    def test_author_token_can_query_demo_table(self, e2e_ctx):
        """Author token can run sales aggregate."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT COUNT(*) AS cnt, SUM(nsv) AS total_nsv FROM sales"},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        from tests.e2e.conftest import read_arrow_bytes
        rows = read_arrow_bytes(resp.content)
        assert rows[0]["cnt"] > 0
        # Known total from seed_data/parquet is ~22.3M
        assert rows[0]["total_nsv"] > 15_000_000
