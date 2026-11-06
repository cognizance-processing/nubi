"""The AI run_query tool parses SQL in the dialect of the datastore it targets."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.ai import tools
from app.connectors.dialects import DEFAULT_DIALECT
from app.routes.connectors import DEMO_CONNECTOR_ID

ORG = "org-1"
DATASTORE = "ds-1"


class _Repo:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error

    async def get(self, _table, _org, _id):
        if self.error:
            raise self.error
        return self.row


def _dialect_with(row=None, error=None):
    with patch("app.repos.provider.get_repo", return_value=_Repo(row, error)):
        return tools._datastore_dialect(DATASTORE, ORG)


@pytest.mark.parametrize(
    ("datastore_id", "org_id"),
    [(None, ORG), ("", ORG), (DEMO_CONNECTOR_ID, ORG), (DATASTORE, None)],
)
def test_no_real_datastore_uses_the_default_dialect(datastore_id, org_id):
    with patch("app.repos.provider.get_repo") as get_repo:
        assert tools._datastore_dialect(datastore_id, org_id) == DEFAULT_DIALECT
    get_repo.assert_not_called()


def test_warehouse_datastore_uses_its_own_dialect():
    assert _dialect_with({"config": {"connector_type": "bigquery"}}) == "bigquery"


def test_the_older_type_key_is_honoured():
    assert _dialect_with({"config": {"type": "bigquery"}}) == "bigquery"


def test_unknown_datastore_falls_back_to_the_default():
    assert _dialect_with(None) == DEFAULT_DIALECT


def test_repo_failure_falls_back_to_the_default():
    assert _dialect_with(error=RuntimeError("db down")) == DEFAULT_DIALECT
