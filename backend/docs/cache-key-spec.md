# Nubi Cache-Key Specification

> Version: 1  
> Implementation: `app/connectors/cache_key.py`  
> Conformance test: `tests/conformance/test_conformance.py::TestCacheKeySpecVectors`

The cache key uniquely identifies a physical query plan so that identical
queries with identical RLS claims can share cached results.  The algorithm is
frozen: changing it invalidates all existing cache entries and requires bumping
`CACHE_KEY_VERSION` in `cache_key.py`.

---

## Algorithm

1. **Extract RLS policies** from `rls_claims.get("policies", {})`.  
   Only the `"policies"` sub-dict is used.  All other JWT claims (`sub`, `exp`,
   `iat`, etc.) are excluded so that token expiry differences do not cause cache
   misses for otherwise identical data access patterns.

2. **Sort policy keys** lexicographically.  This makes the key
   order-independent: `{"region": "us-east", "tenant_id": "acme"}` produces the
   same result as `{"tenant_id": "acme", "region": "us-east"}`.

3. **Build the canonical dict**:
   ```python
   {"sql": sql, "params": params, "rls": sorted_policies}
   ```

4. **Serialise to canonical JSON**:
   ```python
   json.dumps(canonical, sort_keys=True, separators=(',', ':'))
   ```
   `sort_keys=True` ensures dict-key order never affects the output.
   `separators=(',', ':')` removes all whitespace.

5. **UTF-8 encode** the JSON string.

6. **SHA-256 hash** the bytes and return the 64-character lowercase hex digest.

### Python reference implementation

```python
import hashlib, json

def compute_cache_key(sql, params, rls_claims):
    policies = {}
    if rls_claims:
        raw = rls_claims.get("policies", {})
        if isinstance(raw, dict):
            policies = dict(sorted(raw.items()))   # sort by key

    canonical = {"sql": sql, "params": params, "rls": policies}
    blob = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

---

## Test vectors

These four vectors are the conformance contract.  The live implementation in
`compute_cache_key` must produce these exact digests.  They are also asserted
by `tests/conformance/test_conformance.py::TestCacheKeySpecVectors`.

### Vector 1 — simple SELECT, no params, no RLS

```
sql    = "SELECT id, name FROM users"
params = []
rls    = {}
```

Canonical JSON (before hashing):
```
{"params":[],"rls":{},"sql":"SELECT id, name FROM users"}
```

**Expected key:**
```
2da34f05b16152c531c0a460dc7b0ec722affc90998d44f4fa663b134f487054
```

---

### Vector 2 — params and multi-key RLS (non-policy claims excluded)

```
sql    = "SELECT * FROM orders"
params = [1, "active"]
rls_claims = {
    "sub": "user-123",
    "exp": 9999999999,
    "policies": {
        "tenant_id": "acme",
        "region":    "us-east",
    },
}
```

Only `policies` is included; `sub` and `exp` are discarded.  Policy keys are
sorted: `region` before `tenant_id`.

Canonical JSON:
```
{"params":[1,"active"],"rls":{"region":"us-east","tenant_id":"acme"},"sql":"SELECT * FROM orders"}
```

**Expected key:**
```
fce38ca3a5d762ab04bfe02ce6f5cfc32dccb4b0ed294cf0feeb24865ed2fd31
```

---

### Vector 3 — policy key order must not affect the cache key

Both of these inputs must produce the same key:

```
# Input A
sql    = "SELECT id FROM events"
params = []
rls    = {"policies": {"region": "us-east", "tenant_id": "acme"}}

# Input B  (keys swapped)
sql    = "SELECT id FROM events"
params = []
rls    = {"policies": {"tenant_id": "acme", "region": "us-east"}}
```

**Expected key (both A and B):**
```
1caaa6835929382919e423ee76bd14566ea8acb9d12b703e1876db27fd66a185
```

---

### Vector 4 — integer policy value serialises as a bare JSON number

```
sql    = "SELECT * FROM logs WHERE level > 2"
params = [2]
rls    = {"policies": {"org_id": 42}}
```

`org_id` is an integer; `json.dumps` serialises it as `42` (not `"42"`).

Canonical JSON:
```
{"params":[2],"rls":{"org_id":42},"sql":"SELECT * FROM logs WHERE level > 2"}
```

**Expected key:**
```
fa39b9faa32aa1bf8763fe9adee97046388f3fe070f85c444f3defa17321e32c
```

---

## Versioning

`CACHE_KEY_VERSION = "1"` is a documentation constant in `cache_key.py`.  It
is **not** included in the hash input — the algorithm structure implicitly
encodes the version.

If the algorithm changes (different dict structure, different serialisation,
different hash function), you MUST:

1. Bump `CACHE_KEY_VERSION` to `"2"` (or higher).
2. Recompute all four vectors and update this document.
3. Recompute all frozen `expected_cache_key` values in `tests/conformance/cases.py`.
4. Invalidate or migrate any existing cache store.
