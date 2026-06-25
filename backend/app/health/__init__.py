"""Data health & freshness sub-package.

Public surface
--------------
- ``app.health.store``   — freshness registry (InMemoryFreshnessStore / PgFreshnessStore)
- ``app.health.scoring`` — weighted health-score computation
- ``app.routes.health``  — HTTP routes (GET /health/freshness, /health/score, /health/estate)
"""
