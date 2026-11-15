"""Flow materializations are written inside the owning organisation's storage root.

The root is ``<operator base>/<org id>/``; nothing a flow author supplies can
move it, and the read side allows exactly the same root.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"


def _settings(base) -> SimpleNamespace:
    return SimpleNamespace(FLOWS_MATERIALIZE_BASE_URI=str(base))


def _resolve(mat, org, base, flow_extra=None):
    from app.flows.incremental import resolve_target_uri

    flow = {"id": "f1", "org_id": org, **(flow_extra or {})}
    return resolve_target_uri("prod", mat, flow, _settings(base))


def test_same_target_in_two_orgs_lands_in_two_places(tmp_path):
    a = _resolve({"kind": "full", "target": "sales/daily"}, ORG_A, tmp_path)
    b = _resolve({"kind": "full", "target": "sales/daily"}, ORG_B, tmp_path)
    assert a == os.path.join(str(tmp_path), ORG_A, "prod", "sales", "daily.parquet")
    assert b == os.path.join(str(tmp_path), ORG_B, "prod", "sales", "daily.parquet")


def test_object_storage_targets_are_namespaced_too():
    uri = _resolve({"kind": "full", "target": "sales/daily"}, ORG_A, "s3://bucket/lake/")
    assert uri == f"s3://bucket/lake/{ORG_A}/prod/sales/daily.parquet"


@pytest.mark.parametrize(
    "where",
    ["/tmp/elsewhere", "s3://someone-elses-bucket/x", "s3://bucket/lake", "gs://bucket/x", "../up"],
)
def test_a_flow_cannot_choose_its_own_base(tmp_path, where):
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        _resolve({"kind": "full", "target": "t", "base_uri": where}, ORG_A, tmp_path)
    assert exc.value.status == 400
    for flow_extra in (
        {"runtime_config": {"materialize_base_uri": where}},
        {"spec": {"runtime_config": {"materialize_base_uri": where}}},
    ):
        with pytest.raises(AppError) as exc:
            _resolve({"kind": "full", "target": "t"}, ORG_A, tmp_path, flow_extra)
        assert exc.value.status == 400


def test_a_run_with_no_organisation_writes_nothing(tmp_path):
    from app.errors import AppError
    from app.flows.incremental import resolve_target_uri

    for flow in (None, {}, {"id": "f1"}, {"org_id": ""}, {"org_id": "../" + ORG_B}, {"org_id": "a/b"}):
        with pytest.raises(AppError) as exc:
            resolve_target_uri("prod", {"kind": "full", "target": "t"}, flow, _settings(tmp_path))
        assert exc.value.status == 400


def test_target_cannot_climb_into_another_org(tmp_path):
    from app.errors import AppError

    for target in (f"../{ORG_B}/prod/t", f"../../{ORG_B}/t", f"/{ORG_B}/t/../../x"):
        try:
            uri = _resolve({"kind": "full", "target": target}, ORG_A, tmp_path)
        except AppError as exc:
            assert exc.status == 400
            continue
        assert uri.startswith(os.path.join(str(tmp_path), ORG_A) + os.sep)


def test_reader_allows_exactly_the_writers_root(tmp_path, monkeypatch):
    from app.connectors import duckdb_conn
    from app.flows import incremental

    monkeypatch.delenv("NUBI_DUCKDB_DATA_DIRS", raising=False)  # the suite opens the temp dir to every org
    monkeypatch.setattr(incremental, "_operator_base", lambda settings=None: str(tmp_path))
    written = _resolve({"kind": "full", "target": "sales/daily"}, ORG_A, tmp_path)
    assert duckdb_conn._target_problem(written, {}, ORG_A) is None
    assert duckdb_conn._target_problem(written, {}, ORG_B) is not None

    monkeypatch.setattr(incremental, "_operator_base", lambda settings=None: "s3://bucket/lake")
    monkeypatch.delenv("NUBI_S3_ORG_PREFIX", raising=False)
    written = _resolve({"kind": "full", "target": "sales/daily"}, ORG_A, "s3://bucket/lake")
    assert duckdb_conn._target_problem(written, {}, ORG_A) is None
    assert duckdb_conn._target_problem(written, {}, ORG_B) is not None
    assert duckdb_conn._target_problem(f"s3://bucket/lake/{ORG_A}x/prod/t.parquet", {}, ORG_A) is not None


def test_a_watermark_without_its_target_is_not_used(tmp_path):
    from app.flows.incremental import usable_watermark

    target = tmp_path / ORG_A / "prod" / "t.parquet"
    assert usable_watermark("2026-01-01T00:00:00", str(target)) is None
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    assert usable_watermark("2026-01-01T00:00:00", str(target)) == "2026-01-01T00:00:00"
    assert usable_watermark(None, str(target)) is None
    # Object storage is not probed here: the stored mark is kept.
    assert usable_watermark("2026-01-01T00:00:00", "s3://bucket/x/t.parquet") == "2026-01-01T00:00:00"
