"""EMAIL_ALLOWED_PURPOSES: an environment can limit which kinds of email are
really sent (for example password resets only), and every place that sends
email says which kind it sends.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.jobs.report import (
    EMAIL_PURPOSES,
    NullSender,
    SmtpEmailSender,
    allowed_email_purposes,
    get_default_sender,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _settings(**overrides):
    values = {
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": 587,
        "SMTP_USERNAME": "user",
        "SMTP_PASSWORD": "not-a-real-password",
        "SMTP_USE_TLS": True,
        "SMTP_TLS_VERIFY": True,
        "SMTP_FROM": "no-reply@example.test",
        "BILLING_EMAIL": "",
        "COMPANY_EMAIL": "",
        "COMPANY_NAME": "Nubi",
        "EMAIL_ALLOWED_PURPOSES": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


ALL_PURPOSES = [*sorted(EMAIL_PURPOSES), ""]


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_no_mail_server_never_sends(purpose):
    s = _settings(SMTP_HOST="", EMAIL_ALLOWED_PURPOSES="password_reset")
    assert isinstance(get_default_sender(s, purpose=purpose), NullSender)


@pytest.mark.parametrize("purpose", ALL_PURPOSES)
def test_empty_list_sends_every_purpose(purpose):
    assert allowed_email_purposes(_settings()) is None
    assert isinstance(get_default_sender(_settings(), purpose=purpose), SmtpEmailSender)


def test_password_reset_only():
    s = _settings(EMAIL_ALLOWED_PURPOSES="password_reset")
    assert isinstance(get_default_sender(s, purpose="password_reset"), SmtpEmailSender)
    for purpose in ("report", "invoice", "", "something_else"):
        assert isinstance(get_default_sender(s, purpose=purpose), NullSender), purpose


def test_list_ignores_case_and_spaces():
    s = _settings(EMAIL_ALLOWED_PURPOSES=" Password_Reset , report ")
    assert allowed_email_purposes(s) == {"password_reset", "report"}
    assert isinstance(get_default_sender(s, purpose="password_reset"), SmtpEmailSender)
    assert isinstance(get_default_sender(s, purpose="report"), SmtpEmailSender)
    assert isinstance(get_default_sender(s, purpose="invoice"), NullSender)


def test_list_of_only_commas_sends_nothing():
    s = _settings(EMAIL_ALLOWED_PURPOSES=" , ")
    for purpose in ALL_PURPOSES:
        assert isinstance(get_default_sender(s, purpose=purpose), NullSender), purpose


def test_setting_exists_and_defaults_to_empty():
    from app.config import Settings

    assert Settings.model_fields["EMAIL_ALLOWED_PURPOSES"].default == ""


def _sender_calls():
    """Every get_default_sender(...) call under app/, with its purpose."""
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name != "get_default_sender":
                continue
            purpose = None
            for kw in node.keywords:
                if kw.arg == "purpose" and isinstance(kw.value, ast.Constant):
                    purpose = kw.value.value
            yield path.relative_to(APP_DIR).as_posix(), node.lineno, purpose


def test_every_sender_call_names_a_known_purpose():
    calls = list(_sender_calls())
    assert calls, "expected get_default_sender calls under app/"
    for path, line, purpose in calls:
        assert purpose in EMAIL_PURPOSES, f"{path}:{line} sends email without a known purpose"


def test_only_the_reset_route_sends_password_reset_email():
    # A report or invoice path labelled "password_reset" would slip through an
    # environment that allows resets only, so pin each path to its purpose.
    found: dict[str, set] = {}
    for path, _line, purpose in _sender_calls():
        found.setdefault(path, set()).add(purpose)
    assert found == {
        "routes/auth.py": {"password_reset"},
        "jobs/executor.py": {"report"},
        "flows/handlers/report_send.py": {"report"},
        "ee/billing/invoice_email.py": {"invoice"},
    }
