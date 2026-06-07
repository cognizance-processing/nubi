"""Nubi backend security / penetration test suite.

This package contains adversarial tests across seven attack classes:

1. test_sec_embed_tokens.py  — embed token forgery / algorithm confusion
2. test_sec_rls.py           — RLS / claim override
3. test_sec_sql_injection.py — SQL injection via named params / substitution
4. test_sec_authz.py         — cross-tenant authorisation
5. test_sec_chat_webhooks.py — chat webhook signature auth
6. test_sec_connector.py     — connector / planner hardening
7. test_sec_auth_sessions.py — refresh-token reuse / logout invalidation

CRITICAL HONESTY RULE: where a protection is MISSING or weaker than expected
the test is marked ``@pytest.mark.xfail(reason="SECURITY GAP: ...")`` so
the gap is surfaced rather than hidden.
"""
