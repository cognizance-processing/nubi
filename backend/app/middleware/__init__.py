"""App-level middleware package.

Currently ships:
- ratelimit  — in-process token-bucket rate limiting by (org_or_ip, route_class).
               Configure via NUBI_RATELIMIT_* env vars; globally disabled by
               NUBI_RATELIMIT_ENABLED=false.  See ratelimit.py for full docs.
"""
