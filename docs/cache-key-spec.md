# Cache-Key Specification

![Result cache keyed on SQL, params, and RLS policies](illustration:EdgeCache)

> **Stability: frozen.** Breaking changes require a version bump in
> `backend/app/connectors/cache_key.py:CACHE_KEY_VERSION` and updated conformance
> vectors. This document is the binding contract between the Python planner and
> any future executor implementation (Rust, WASM, or otherwise).

---

## Purpose

The cache key is a content-addressable identifier for the *effective* data-access
pattern of a query. Two queries that access the same rows under the same RLS
constraints collapse to a single warehouse hit and share cached results.

The key is deliberately **independent** of non-data-affecting JWT claims (`exp`,
`iat`, `sub`) so that token rotation never causes unnecessary cache misses.

---

## Algorithm

```
cache_key = SHA-256( canonical_json )
```

where `canonical_json` is:

```python
json.dumps(
    {
        "sql":    <rewritten SQL string>,
        "params": <ordered list of positional params>,
        "rls":    <policies dict, keys sorted lexicographically>,
    },
    sort_keys=True,
    separators=(",", ":"),   # compact — no whitespace
)
```

encoded to UTF-8 before hashing. The output is a **64-character lowercase hex string**.

### Inputs

| Field in canonical JSON | Type | Description |
|---|---|---|
| `sql` | string | The rewritten SQL string **after** projection and RLS predicate injection. Not the original logical query. |
| `params` | array | Ordered positional parameters bound to `$N` / `?` / `%s` placeholders. Empty array `[]` if none. |
| `rls` | object | Only `claims["policies"]` — the `{column: value}` dict consumed by the predicate injector. Keys sorted lexicographically. All other JWT claims are excluded. |

### Canonical JSON rules

1. **Sorted keys** — `sort_keys=True` at every nesting level. The `rls` dict
   keys are also explicitly sorted before serialisation (`dict(sorted(policies.items()))`).
2. **No whitespace** — `separators=(",", ":")`.
3. **UTF-8 encoding** — the JSON string is encoded to bytes before hashing.
4. **Stable JSON types** — `bool` serialises as `true`/`false`, integers as bare
   numbers, strings as double-quoted. No `null` values in `rls` (omit absent
   policies entirely).

### RLS claim extraction

The RLS subset is `claims.get("policies", {})`. Any claim outside `"policies"`
(e.g. `"sub"`, `"exp"`, `"iat"`, `"roles"`) is **not included** in the key. This
is the same subset the predicate injector consumes — one source of truth ensures
the cache key and the predicate set never diverge.

### Version constant

`CACHE_KEY_VERSION = "1"` in `cache_key.py` is a documentation constant. It is
**not** part of the hash input — the algorithm structure implicitly encodes the
version. Bump it if the algorithm changes; doing so signals that existing cache
entries are invalid.

---

## Python reference implementation

```python
import hashlib
import json

def compute_cache_key(sql: str, params: list, rls_claims: dict) -> str:
    """Return the SHA-256 hex cache key for a physical plan."""
    policies: dict = {}
    if rls_claims:
        raw = rls_claims.get("policies", {})
        if isinstance(raw, dict):
            policies = dict(sorted(raw.items()))   # sort by key

    canonical = {"sql": sql, "params": params, "rls": policies}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

---

## Test vectors

These vectors **must** be reproduced byte-for-byte by any conformant implementation.
They are also asserted by the live test suite at
`backend/tests/test_cache_key.py` and the planner conformance suite at
`backend/tests/conformance/`. See [/docs/conformance](/docs/conformance) for the
full end-to-end conformance contract.

---

### Vector 1 — simple SELECT, no params, no RLS

**Function call inputs**

```python
compute_cache_key(
    sql    = "SELECT id, name FROM users",
    params = [],
    rls_claims = {},
)
```

**Canonical JSON** (the UTF-8 string that is hashed)

```
{"params":[],"rls":{},"sql":"SELECT id, name FROM users"}
```

**Expected SHA-256**

```
2da34f05b16152c531c0a460dc7b0ec722affc90998d44f4fa663b134f487054
```

---

### Vector 2 — params and multi-key RLS (non-policy claims excluded)

`sub` and `exp` are present in the claims but are discarded; only `policies` enters
the canonical JSON. Policy keys are sorted: `region` before `tenant_id`.

**Function call inputs**

```python
compute_cache_key(
    sql    = "SELECT * FROM orders",
    params = [1, "active"],
    rls_claims = {
        "sub": "user-123",
        "exp": 9999999999,
        "policies": {
            "tenant_id": "acme",
            "region":    "us-east",
        },
    },
)
```

**Canonical JSON**

```
{"params":[1,"active"],"rls":{"region":"us-east","tenant_id":"acme"},"sql":"SELECT * FROM orders"}
```

**Expected SHA-256**

```
fce38ca3a5d762ab04bfe02ce6f5cfc32dccb4b0ed294cf0feeb24865ed2fd31
```

---

### Vector 3 — policy key insertion order is irrelevant

Both inputs below must produce the same key.

**Input A** — `region` first

```python
compute_cache_key(
    sql    = "SELECT id FROM events",
    params = [],
    rls_claims = {"policies": {"region": "us-east", "tenant_id": "acme"}},
)
```

**Input B** — `tenant_id` first (keys swapped)

```python
compute_cache_key(
    sql    = "SELECT id FROM events",
    params = [],
    rls_claims = {"policies": {"tenant_id": "acme", "region": "us-east"}},
)
```

**Canonical JSON** (identical for both — keys sorted)

```
{"params":[],"rls":{"region":"us-east","tenant_id":"acme"},"sql":"SELECT id FROM events"}
```

**Expected SHA-256** (same for both A and B)

```
1caaa6835929382919e423ee76bd14566ea8acb9d12b703e1876db27fd66a185
```

---

### Vector 4 — integer policy value

Integer policy values serialise as bare JSON numbers, not quoted strings.

**Function call inputs**

```python
compute_cache_key(
    sql    = "SELECT * FROM logs WHERE level > 2",
    params = [2],
    rls_claims = {"policies": {"org_id": 42}},
)
```

**Canonical JSON**

```
{"params":[2],"rls":{"org_id":42},"sql":"SELECT * FROM logs WHERE level > 2"}
```

**Expected SHA-256**

```
fa39b9faa32aa1bf8763fe9adee97046388f3fe070f85c444f3defa17321e32c
```

---

## Required properties

| Property | Requirement |
|---|---|
| **Stability** | Same inputs → same key across processes, restarts, and Python versions. |
| **Non-colliding RLS** | Changing any policy value changes the key. |
| **JWT-claim isolation** | Adding or changing non-`policies` JWT claims (`exp`, `sub`, etc.) does **not** change the key. |
| **Order independence** | Reordering `policies` dict keys does **not** change the key (see Vector 3). |
| **Type sensitivity** | `"42"` (string) ≠ `42` (integer) — they produce different canonical JSON and different keys. |

---

## Rust / WASM conformant implementation

```rust
fn compute_cache_key(sql: &str, params: &[Value], policies: &BTreeMap<String, Value>) -> String {
    // BTreeMap is sorted by key — no explicit sort needed.
    let canonical = json!({
        "sql":    sql,
        "params": params,
        "rls":    policies,
    });
    // Compact output (no whitespace); BTreeMap ensures deterministic key order.
    let canonical_str = serde_json::to_string(&canonical).unwrap();
    let digest = sha256(canonical_str.as_bytes());
    hex::encode(digest)
}
```

Key points:
- Use `BTreeMap` (sorted) for `policies` so key order is deterministic.
- Use `serde_json` compact output (not pretty-print).
- Outer JSON object keys are also sorted: `params` < `rls` < `sql` (ASCII order,
  matching Python's `sort_keys=True`).
- Encode the JSON string to UTF-8 bytes before hashing.
- Output lowercase hex (64 characters).

---

## Version history

| Version | Change |
|---|---|
| `1` | Initial frozen spec. SHA-256 of canonical JSON with sorted keys, no whitespace, UTF-8. |

---

## Making a breaking change

If you must change the algorithm:

1. Bump `CACHE_KEY_VERSION` in `backend/app/connectors/cache_key.py`.
2. Recompute all four test vectors above and update this document.
3. Recompute all frozen `expected_cache_key` values in `backend/tests/conformance/cases.py`.
4. Invalidate or migrate any existing cache store.
