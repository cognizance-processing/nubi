# Governance — RLS, scopes, and hierarchical expansion

Nubi enforces data access governance at three layers:

1. **Row-level security (RLS)** — JWT policy claims are injected as AST
   predicates by the planner. No string concatenation; injected at the
   `WHERE`-clause AST level.
2. **Authoring scopes** — token-level capability gates controlling raw SQL
   access and metric authoring.
3. **Hierarchical scope expansion** — a parent policy value (e.g.
   `region = "Western Cape"`) is expanded to its registered child values
   (e.g. `store_id IN (10, 11, 12)`) before predicate injection.

All governance is **token-only** and **fail-closed**: if the policy cannot be
verified it is enforced as-if maximally restrictive, never ignored.

---

## RLS policy schema

RLS policies come from the `policies` claim in the verified JWT:

```json
{
  "policies": {
    "tenant_id": "acme",
    "region": "Western Cape"
  }
}
```

The planner reads this dict and builds one predicate per key. Three predicate
shapes are supported:

### Scalar (equality)

A plain string or number value produces an equality predicate:

```json
{ "tenant_id": "acme" }
```
→ `WHERE tenant_id = 'acme'`

### List (IN)

A JSON array produces an IN-list predicate:

```json
{ "status": ["active", "trial"] }
```
→ `WHERE status IN ('active', 'trial')`

### Range dict (gte / gt / lte / lt)

A dict with one or more comparison keys produces range predicates:

```json
{ "score": { "gte": 0.5, "lt": 1.0 } }
```
→ `WHERE score >= 0.5 AND score < 1.0`

Supported keys: `gte`, `gt`, `lte`, `lt`. All four can be combined.

---

## Hierarchical scope expansion

A scalar policy value can be transparently expanded to a list of child values
when the org has registered a hierarchy for that dimension in the
`access_hierarchy` table.

For example, if `region = "Western Cape"` has children `[10, 11, 12]`
registered in the hierarchy, the planner receives a list instead of a scalar
and emits an IN predicate:

```
region = "Western Cape"
  → (hierarchy lookup, org-scoped)
  → store_id IN (10, 11, 12)
```

**Security contract:**

- `resolve()` always filters by `org_id` taken from the verified token — never
  from request input.
- The `access_hierarchy` table is populated by trusted platform/admin tooling
  only.
- If a dimension has no children registered, the original scalar value is
  returned unchanged (non-hierarchical dimensions pass through transparently).
- Resolution output is used to build AST-level IN predicates (`_make_in_predicate`
  in the planner) — never string-concatenated.

---

## Scope-resolution endpoint — `GET /auth/scope`

A host (or a host frontend) calls `GET /api/v1/auth/scope` to discover what a
token — first-party **or** embed — is actually authorised to see, without having
to re-derive it. The endpoint resolves the caller's scope **from the verified
token only**:

```json
{
  "org": "<org_id>",
  "scope": ["read:*"],
  "policies": { "region": "Gauteng" },
  "effective_policies": { "region": ["Gauteng", "JHB", "PTA"] },
  "expanded": true
}
```

- `policies` — the **raw** policy claim carried by the verified token.
- `effective_policies` — the raw policies **hierarchy-expanded** (via
  `expand_rls_policies`) **and merged** with any non-expired `access_grants` for
  the caller's subject, normalised to `{dimension: [values]}`.
- `expanded` — `true` when `effective_policies` differs from the raw policies.

**Security contract:**

- Org, scope, and policies come **only** from the verified token — a request
  body is ignored.
- Resolution is **org-scoped** (hierarchy lookup and grant merge both filter on
  the token's org).
- **Fail-closed:** on any resolution error the endpoint returns the narrower raw
  policies — it never widens the effective set.

---

## Access grants — `/access-grants`

`access_grants` (migration 0022) is the optional "user → scope assignment" store:
org-scoped grants of a `(dimension, value)` pair to a subject (`user` / `role` /
`embed_sub`), with an optional `expires_at`. A host may either mint policies
directly into the embed token **or** store grants here and let `GET /auth/scope`
merge them in (token policies ∪ stored grants, per dimension).

| Method | Path | Gate |
| --- | --- | --- |
| `GET` | `/access-grants?subject_type=&subject_id=` | any org member |
| `POST` | `/access-grants` | owner/admin |
| `DELETE` | `/access-grants/{id}` | owner/admin |

**Security contract:**

- Every operation is scoped to the caller's org (resolved server-side — never
  from the request body). The `POST` body sets the grant **target**; it can
  never set the caller's own scope.
- Writes are gated to approver roles (owner/admin), mirroring `/admin/*`.
- A grant id belonging to another org (or absent) returns **404, not 403**, so a
  grant's existence is not enumerable across tenants.
- The planner never reads `access_grants` directly — only `GET /auth/scope` does,
  and only for the caller's own subject.

---

## RLS policy cardinality cap

`NUBI_RLS_MAX_POLICY_VALUES` (default **5000**) is a hard ceiling on the number
of values a **single** RLS policy may resolve to. It applies to:

- the number of values in an explicit **IN-list** policy, and
- the **output of hierarchy expansion** for a scalar policy.

When the count exceeds the cap the planner **fails closed** with
`AppError("rls_policy_too_large", …, 400)`. This is deliberate: silently
truncating the list would **drop predicates and widen** the rows a caller can
see, and an unbounded IN list is a DoS / pathological-plan risk. Large-tenant
deployments may raise the cap intentionally via the setting. Enforced in
`_make_in_predicate` and `expand_rls_policies`.

---

## Authoring scopes

Authoring scopes gate write/authoring capabilities. They are carried in the
`scope` claim of the JWT and use the `action:resource` format.

### `author:sql`

Permits execution of arbitrary raw SQL via `POST /query` (and via the
`run_query` MCP/agent tool with an ad-hoc `sql` argument).

**Fail-closed:** if the token lacks `author:sql` (and the caller is not
resolving a registered `query_id`), the server returns 403
`insufficient_scope`. Embed tokens (`kind: "embed"`) are **always** blocked
from raw SQL by the M3-SEC allowlist gate, independent of whether they carry
this scope.

### `author:metric`

Permits creating, editing, and registering metrics via `POST /metrics` etc.
Embed tokens may carry this scope to allow governed metric authoring.

**First-party login JWTs** carry both `author:sql` and `author:metric`
automatically. Read-only or embed-end-user tokens omit both.

---

## Scope wildcard rules

The `has_scope(scopes, required)` helper supports trailing wildcards:

| Scope in token | Matches |
|---------------|---------|
| `read:*` | `read:anything`, `read:a:b` |
| `read:dashboard:*` | `read:dashboard:abc` but NOT `read:other:abc` |
| `*` | every scope (super-admin sentinel) |

---

## Agent / env-scoped write tokens

Agent-scoped tokens can carry `write:<resource>:<env>` scopes to restrict an
automated caller to a single environment (e.g. `write:board:dev`). The
`require_env_write` helper enforces:

- A token with NO `write:` scope → full-access first-party caller → pass through.
- A token WITH `write:` scopes → scoped token → must hold
  `write:<resource>:<env>` or a broader wildcard to write to that env.
- **Protected environments** (e.g. `prod`) require a resource-wide or global
  wildcard; an exact env-scoped token cannot reach a protected env.

---

## Embed token security

Embed tokens (`kind: "embed"`) have additional restrictions enforced by the
M3-SEC allowlist gate:

- **Always blocked from raw SQL** — even if `author:sql` is present.
- Must reference server-registered `query_id` or `metric_id` values.
- RLS policies in the embed token are enforced identically to first-party tokens.

---

## Token fields that drive governance

| JWT claim | Where it comes from | What it gates |
|-----------|--------------------|----|
| `policies` | Issuer-configured per viewer | RLS predicate injection |
| `scope` | Token minting | `author:sql`, `author:metric`, `write:*`, wildcard |
| `kind` | Token type | `"embed"` → raw SQL always blocked |
| `org` / `org_id` | Verified identity | All org-scoped DB queries |

All claims are read from the **verified** token only. The server never takes
org or policy values from the request body on any auth-sensitive path.
