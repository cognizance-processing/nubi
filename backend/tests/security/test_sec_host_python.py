"""User-supplied Python never runs on the application host in production."""

from __future__ import annotations

import subprocess
import uuid

import pytest

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG = str(uuid.uuid4())

PY_SPEC = {
    "name": "t",
    "tasks": [{"key": "cell", "kind": "python", "config": {"code": "result = {'ok': 1}"}}],
}
SDK_SOURCE = '''
from nubi.flows import flow, task

@task(kind="query", sql="SELECT 1")
def load():
    pass

@flow
def demo():
    load()

spec = demo.compile()
'''


@pytest.fixture
def production(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "KERNEL_REMOTE_PROVIDER", "")
    monkeypatch.setattr(settings, "KERNEL_LOCAL_ENABLED", True)

    def _no_spawn(*a, **k):
        raise AssertionError("a subprocess was spawned on the host")

    monkeypatch.setattr(subprocess, "Popen", _no_spawn)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.auth.deps import verified_identity
    from app.auth.verify import VerifiedIdentity
    from app.repos.provider import set_repo

    app, _repo, users = make_tenant_app({"a": (ORG, "owner")}, modules=("app.routes.flows", "app.routes.compute"))
    app.dependency_overrides[verified_identity] = lambda: VerifiedIdentity(
        kind="access", user_id=users["a"], org=ORG, project=None, roles=[], policies={},
        scope=["*"], embed_origin=None, datastore=None, raw_claims={},
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    set_repo(None)


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/flows/compile", {"code": SDK_SOURCE}),
        ("/api/v1/flows/preview", {"spec": PY_SPEC}),
        ("/api/v1/flows/run-cell", {"spec": PY_SPEC}),
        ("/api/v1/compute/run", {"code": "result = None", "inputs": {}}),
    ],
)
def test_endpoints_refuse_in_production_without_a_remote_sandbox(client, production, path, body):
    resp = client.post(path, json=body, headers=as_user("a"))
    assert resp.status_code == 503, resp.text


def test_sandbox_primitive_refuses_in_production(production):
    from app.compute.sandbox import run_sandboxed
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        run_sandboxed(["python", "-c", "print(1)"], env={})
    assert exc.value.status == 503


def test_python_task_handler_refuses_in_production(production):
    """Covers scheduled / worker-driven runs, which never pass through a route."""
    from datetime import datetime, timezone

    from app.errors import AppError
    from app.flows.executor import TaskContext
    from app.flows.registry import get_task_kind_registry

    handler = get_task_kind_registry().get("python")
    ctx = TaskContext(flow_params={}, inputs={}, now=datetime.now(timezone.utc), secrets={})
    with pytest.raises(AppError) as exc:
        handler(config={"code": "result = 1"}, ctx=ctx, claims={})
    assert exc.value.status == 503


def test_local_execution_needs_the_explicit_dev_flag(monkeypatch):
    from app.compute.policy import host_python_allowed
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "KERNEL_LOCAL_ENABLED", False)
    assert host_python_allowed() is False
    monkeypatch.setattr(settings, "KERNEL_LOCAL_ENABLED", True)
    assert host_python_allowed() is True
    monkeypatch.setattr(settings, "ENV", "production")
    assert host_python_allowed() is False


def test_development_still_compiles(client):
    resp = client.post("/api/v1/flows/compile", json={"code": SDK_SOURCE}, headers=as_user("a"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["spec"]["tasks"]


@pytest.mark.parametrize(
    "expr",
    [
        "().__class__.__mro__[1].__subclasses__()",
        "inputs.__class__",
        "[c for c in ().__class__.__base__.__subclasses__()]",
        "(lambda: 1)()",
        "'{0.__class__}'.format(inputs)",
        "getattr(inputs, 'x')",
        "__import__('os').system('id')",
    ],
)
def test_branch_conditions_cannot_reach_interpreter_internals(expr):
    from datetime import datetime, timezone

    from app.flows.executor import TaskContext
    from app.flows.handlers.branch import _eval_when

    ctx = TaskContext(flow_params={}, inputs={"x": 1}, now=datetime.now(timezone.utc), secrets={})
    with pytest.raises(ValueError):
        _eval_when(expr, ctx, 0)
    # Inside braces a bare dotted path is only ever a dict lookup (falsy here);
    # everything else must be refused.
    try:
        assert _eval_when("{{ " + expr + " }}", ctx, 0) is False
    except ValueError:
        pass


def test_ordinary_branch_conditions_still_work():
    from datetime import datetime, timezone

    from app.flows.executor import TaskContext
    from app.flows.handlers.branch import _eval_when

    ctx = TaskContext(
        flow_params={"limit": 5},
        inputs={"classify": {"label": "high", "score": 7, "tags": ["a", "b"]}},
        now=datetime.now(timezone.utc),
        secrets={},
    )
    assert _eval_when("{{ inputs.classify.label }} == 'high'", ctx, 0) is True
    assert _eval_when("{{ inputs.classify.score }} > 10", ctx, 0) is False
    assert _eval_when("inputs['classify']['score'] >= params['limit'] and len(inputs['classify']['tags']) == 2", ctx, 0) is True
    assert _eval_when("{{ inputs['classify']['label'] in ('high', 'mid') }}", ctx, 0) is True
    assert _eval_when("True", ctx, 0) is True
