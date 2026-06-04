"""Argon2id password hashing and verification.

Uses argon2-cffi's ``PasswordHasher`` with secure default parameters
(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16).

Public API
----------
hash_password(plain: str) -> str
    Return an argon2id hash string safe to store in the DB.

verify_password(hashed: str, plain: str) -> bool
    Return True if *plain* matches *hashed*; False on mismatch.
    Never raises — VerifyMismatchError and all argon2 exceptions are caught.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

# ── PasswordHasher singleton (thread-/coroutine-safe; stateless) ─────────────
# Parameters follow OWASP recommendations for argon2id:
#   time_cost   = 3   (iterations)
#   memory_cost = 64 MiB (65536 KiB)
#   parallelism = 4
#   hash_len    = 32 bytes
#   salt_len    = 16 bytes
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain: str) -> str:
    """Hash *plain* with argon2id and return the encoded hash string.

    Parameters
    ----------
    plain:
        The raw plaintext password supplied by the user.

    Returns
    -------
    str
        The full argon2id hash string (includes algorithm, params, salt, hash).
        Store this directly in ``users.password_hash``.
    """
    return _ph.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    """Verify *plain* against the stored *hashed* value.

    Parameters
    ----------
    hashed:
        The argon2id hash string retrieved from ``users.password_hash``.
    plain:
        The raw plaintext password to check.

    Returns
    -------
    bool
        ``True`` if the password matches; ``False`` otherwise.

    Notes
    -----
    All argon2 exceptions (mismatch, invalid hash, verification error) are
    caught and collapsed to ``False`` so callers never leak error detail.
    """
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False
