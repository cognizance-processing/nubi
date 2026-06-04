"""Auth package for Nubi.

Exposes the core auth helpers:
- passwords  — argon2id hash / verify
- jwt        — HS256 access token mint / decode
- sessions   — opaque refresh token issue / rotate / revoke
- cookies    — HttpOnly refresh-cookie helpers
- google     — Authorization Code + PKCE OAuth flow
- deps       — FastAPI dependency: current_user
"""
