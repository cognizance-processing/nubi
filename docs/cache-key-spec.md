# Nubi Cache-Key Specification — v1

> **Stability:** Frozen. Breaking changes require a version bump in
> `app/connectors/cache_key.py:CACHE_KEY_VERSION` and new conformance vectors.
> A future Rust/WASM executor MUST produce byte-identical keys for the
> test vectors below. This document is the binding contract between the
> Python planner and any executor implementation.

---

## 1. Purpose

The cache key is a content-addressable identifier for the *effective* data
access pattern of a query. Two queries that access the same rows under the
same RLS constraints must produce the same key so they collapse to a single
warehouse hit. The key is deliberately **independent** of non-data-affecting
JWT claims (e.g. `exp`, `iat`, `sub`) so token rotation does not blow the
cache.

---

## 2. Algorithm

```
cache_key = SHA-256( canonical_json )
```

where `canonical_json` is the UTF-8 encoding of:

```
json.dumps(
    {
        "sql":    <rewritten SQL string>,
        "params": <list of positional params>,
        "rls":    <sorted RLS policies dict>,
    },
    sort_keys=True,
    separators=(",", ":"),   # no whitespace
)
```

### 2.1 Inputs

| Field | Type | Description |
|---|---|---|
| `sql` | `string` | The rewritten SQL string **after** projection and RLS predicate injection. Not the original logical query. |
| `params` | `array` | Ordered positional parameters bound to `$N` / `?` / `%s` placeholders. Empty array `[]` if none. |
| `rls` | `object` | Only `claims["policies"]` — the `{column: value}` dict consumed by the predicate injector. Keys sorted lexicographically. All other JWT claims are excluded. |

### 2.2 Canonical JSON rules

1. **Sorted keys** — `sort_keys=True` at every nesting level. The `rls`
   dict MUST also be sorted by key (the Python implementation uses
   `dict(sorted(policies.items()))` before `json.dumps`).
2. **No whitespace** — `separators=(",", ":")` (compact representation).
3. **UTF-8 encoding** — the JSON string is encoded to bytes before hashing.
4. **Stable JSON types** — `bool` values serialise as `true`/`false`
   (JSON lowercase), integers as bare numbers, strings as double-quoted.
   No `null` values in `rls` (omit absent policies entirely).

### 2.3 Hash

- Algorithm: **SHA-256**
- Output: **64-character lowercase hex string** (not base64).

### 2.4 RLS claim extraction

The RLS subset is `claims.get("policies", {})`. Any claim outside
`"policies"` (e.g. `"sub"`, `"exp"`, `"iat"`, `"roles"`) is
**not included** in the key. This is the same subset the predicate injector
consumes — one source of truth ensures cache-key and predicate set never
diverge.

---

## 3. Worked test vectors

These vectors MUST be reproduced byte-for-byte by any conformant implementation.

---

### Vector 1 — Simple SELECT, no params, no RLS

**Input**

```json
{
  "sql":    "SELECT id, name FROM users",
  "params": [],
  "rls_claims": {}
}
```

**Canonical JSON** (the string that is hashed — shown with `repr()` to expose
every character):

```
'{"params":[],"rls":{},"sql":"SELECT id, name FROM users"}'
```

**Expected SHA-256**

```
2da34f05b16152c531c0a460dc7b0ec722affc90998d44f4fa663b134f487054
```

---

### Vector 2 — SELECT with params and multi-key RLS policies

**Input**

```json
{
  "sql":    "SELECT * FROM orders",
  "params": [1, "active"],
  "rls_claims": {
    "sub":     "user-123",
    "exp":     9999999999,
    "policies": {
      "tenant_id": "acme",
      "region":    "us-east"
    }
  }
}
```

Note: `sub` and `exp` are **excluded** from `rls`; only `policies` is used.

**Canonical JSON**

```
'{"params":[1,"active"],"rls":{"region":"us-east","tenant_id":"acme"},"sql":"SELECT * FROM orders"}'
```

**Expected SHA-256**

```
fce38ca3a5d762ab04bfe02ce6f5cfc32dccb4b0ed294cf0feeb24865ed2fd31
```

---

### Vector 3 — Policy key order is irrelevant (order independence)

Two calls with the same policies in different insertion order must produce the
same key.

**Input A**

```json
{
  "sql":    "SELECT id FROM events",
  "params": [],
  "rls_claims": {
    "policies": { "region": "us-east", "tenant_id": "acme" }
  }
}
```

**Input B** (keys swapped)

```json
{
  "sql":    "SELECT id FROM events",
  "params": [],
  "rls_claims": {
    "policies": { "tenant_id": "acme", "region": "us-east" }
  }
}
```

**Canonical JSON** (same for both, keys sorted)

```
'{"params":[],"rls":{"region":"us-east","tenant_id":"acme"},"sql":"SELECT id FROM events"}'
```

**Expected SHA-256**

```
1caaa6835929382919e423ee76bd14566ea8acb9d12b703e1876db27fd66a185
```

---

### Vector 4 — Integer policy value

**Input**

```json
{
  "sql":    "SELECT * FROM logs WHERE level > 2",
  "params": [2],
  "rls_claims": {
    "policies": { "org_id": 42 }
  }
}
```

**Canonical JSON**

```
'{"params":[2],"rls":{"org_id":42},"sql":"SELECT * FROM logs WHERE level > 2"}'
```

**Expected SHA-256**

```
fa39b9faa32aa1bf8763fe9adee97046388f3fe070f85c444f3defa17321e32c
```

---

## 4. Properties the implementation MUST satisfy

| Property | Test |
|---|---|
| **Stability** | Same inputs → same key across processes, restarts, and Python versions. |
| **Non-colliding RLS** | Changing any policy value changes the key. |
| **JWT-claim isolation** | Adding/changing non-`policies` JWT claims (e.g. `exp`, `sub`) does NOT change the key. |
| **Order independence** | Reordering `policies` dict keys does NOT change the key (covered by Vector 3). |
| **Type sensitivity** | `"42"` (string) ≠ `42` (integer) — they produce different canonical JSON. |

---

## 5. Pseudocode for a Rust conformant implementation

```rust
fn compute_cache_key(sql: &str, params: &[Value], policies: &BTreeMap<String, Value>) -> String {
    // BTreeMap is already sorted by key — no explicit sort needed.
    let canonical = json!({
        "sql":    sql,
        "params": params,
        "rls":    policies,
    });
    // Compact (no whitespace), deterministic key order (serde_json with BTreeMap).
    let canonical_str = serde_json::to_string(&canonical).unwrap();
    let digest = sha256(canonical_str.as_bytes());
    hex::encode(digest)
}
```

Key points for the Rust implementation:
- Use `BTreeMap` (sorted) for the `policies` dict.
- Use `serde_json` with compact output (no pretty-printing).
- The outer JSON object keys are also sorted: `params` < `rls` < `sql`
  (ASCII order — same as Python's `sort_keys=True`).
- Encode the JSON string as UTF-8 bytes before hashing.
- Output lowercase hex (64 chars).

---

## 6. Version history

| Version | Change |
|---|---|
| `1` | Initial frozen spec. SHA-256 of canonical JSON with sorted keys, no whitespace, UTF-8. |
