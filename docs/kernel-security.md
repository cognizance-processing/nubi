# Kernel Security

![The two-kernel trust boundary: browser DuckDB-WASM vs. server Python sandbox](illustration:TrustBoundary)

Nubi runs two kinds of kernels with very different trust boundaries:

- **Browser kernel** — DuckDB-WASM, running entirely inside the visitor's browser tab.
- **Server (Python) kernel** — a metered, scale-to-zero server process for native wheels and large jobs, currently backed by E2B Firecracker microVMs in production.

This document covers the security model, hardening applied, and what you must configure before going to production.

---

## Browser kernel (DuckDB-WASM)

DuckDB-WASM executes entirely in the browser. There is no server process involved for SQL queries routed to the browser tier. The isolation properties come from the browser sandbox itself:

- User code runs in the tab's JavaScript context with no elevated privileges.
- No server secrets, no filesystem access, no cloud IMDS.
- Data never leaves the browser unless the query result is explicitly sent to the server.

SQL cells run against the in-browser DuckDB-WASM instance (`src/lib/wasmRuntime.js`). There is no Python runtime in the browser — every Python cell is sent to the server kernel via `POST /api/v1/compute/run` (`runPythonCell`). The browser tier is SQL-only.

---

## Server kernel (Python subprocess)

Every Python cell is routed to a server kernel — the browser never executes Python. There are two runner implementations.

### Runner selection priority

The route layer (`app/routes/compute.py`) calls `_choose_runner()` which applies this priority order:

| Priority | Condition | Runner |
|---|---|---|
| 1 | `KERNEL_REMOTE_PROVIDER=e2b` + `E2B_API_KEY` set | `E2BRunner` (any env) |
| 2 | `KERNEL_REMOTE_PROVIDER=modal` + Modal credentials set | `ModalRunner` (any env) |
| 3 | `ENV != production` AND `KERNEL_LOCAL_ENABLED=true` | `LocalSubprocessRunner` |
| 4 | Otherwise | 503 `kernel_disabled` |

When `ENV=production` and no remote runner is configured, the local runner is unconditionally blocked and the route returns 503. Setting `KERNEL_LOCAL_ENABLED=true` has no effect in production.

### Scope guard

Every request to the kernel route requires the `exec:kernel` scope. For backwards compatibility with first-party UX tokens, `edit:*` and `*` are treated as implying `exec:kernel`. Missing scope returns 403.

### Code length cap

Code longer than 100 000 characters is rejected with 413 before any subprocess is launched.

---

## E2B runner (production — recommended)

`E2BRunner` (`app/compute/remote_e2b.py`) runs each execution in an isolated Firecracker microVM via the `e2b-code-interpreter` SDK:

- **No host filesystem access** — the VM has its own ephemeral root filesystem; Nubi server files, secrets, and credentials are invisible.
- **No IMDS access** — the VM's network namespace does not include the host's link-local or RFC-1918 addresses, so `169.254.169.254` and `fd00:ec2::254` are unreachable from inside the sandbox.
- **No host process visibility** — the VM kernel is separate; user code cannot see or signal Nubi server processes.
- **Output capped at 64 MiB** for the Arrow IPC result and 1 MiB for captured stdout.
- Each sandbox is killed in a `finally` block after the execution completes.

Enable E2B:

```bash
KERNEL_REMOTE_PROVIDER=e2b
E2B_API_KEY=e2b-...
```

Install the optional dependency:

```bash
pip install e2b-code-interpreter
```

### Modal runner

`ModalRunner` (`app/compute/remote_modal.py`) is a parallel adapter that follows the same `KernelResult` contract and provides comparable container-level isolation (no host IMDS, no host filesystem). The full execution body is not yet implemented — it currently raises 503 and redirects you to use the E2B path. E2B is the primary tested remote runner.

```bash
KERNEL_REMOTE_PROVIDER=modal
MODAL_TOKEN_ID=<your-token-id>
MODAL_TOKEN_SECRET=<your-token-secret>
```

---

## Local subprocess runner (development only)

`LocalSubprocessRunner` (`app/compute/runner.py`) is a development convenience and testbed for the Arrow IPC protocol. It is **never safe for production** — see residual risks below.

### M4-SEC hardening applied

The shared `app/compute/sandbox.py` module (`run_sandboxed`) applies the following to every subprocess, including Flows Python cells:

| Protection | Detail |
|---|---|
| **Env scrubbing** | Subprocess starts from an empty env; only `PATH`, `PYTHONPATH`, `HOME`, `TMPDIR`/`TEMP`/`TMP`, `LANG`/`LC_ALL`/`LC_CTYPE`, `PYTHONUTF8`, `VIRTUAL_ENV` are forwarded. `DATABASE_URL`, `JWT_SECRET`, `AWS_*`, `AZURE_*`, `GCP_*`, `OPENAI_*`, `ANTHROPIC_*` are never forwarded. |
| **Process-group kill** | Child launched with `start_new_session=True`; on timeout, `os.killpg(SIGKILL)` kills the entire process group including orphan grandchildren. Falls back to `proc.kill()` on non-POSIX. |
| **`RLIMIT_CPU`** | CPU seconds = `timeout_s + 2` grace. Skipped when `cpu_limit_s=None` (flows cells with `timeout_s=0`, i.e. no timeout). |
| **`RLIMIT_AS`** | 2 GiB address-space cap. Silently skipped on macOS where the kernel rejects a finite hard cap. |
| **`RLIMIT_FSIZE`** | 128 MiB maximum writable file size. |
| **`RLIMIT_NPROC`** | 64 max simultaneous processes/threads (OS-user-scoped, not subtree). |
| **Output caps** | stdout and stderr each truncated to 1 MiB (bytes, before decode) with a `[... output truncated by nubi kernel cap ...]` marker. |
| **Arrow output cap** | Arrow IPC result file capped at 64 MiB. |
| **Temp cleanup** | `try/finally shutil.rmtree` on all paths (success, error, timeout). |

All limits are overridable via environment variables:

| Variable | Default |
|---|---|
| `KERNEL_RLIMIT_CPU_GRACE_S` | 2 |
| `KERNEL_RLIMIT_AS_BYTES` | 2 147 483 648 (2 GiB) |
| `KERNEL_RLIMIT_FSIZE_BYTES` | 134 217 728 (128 MiB) |
| `KERNEL_RLIMIT_NPROC` | 64 |
| `KERNEL_STDOUT_CAP_BYTES` | 1 048 576 (1 MiB) |
| `KERNEL_STDERR_CAP_BYTES` | 1 048 576 (1 MiB) |

### Flows Python cells

Flows Python cells (`kind='python'` in `app/flows/registry.py`) share the exact same `run_sandboxed` code path — process-group kill, rlimits, output caps, env scrubbing. A cell configured with `timeout_s=0` (no timeout) skips `RLIMIT_CPU` so long-running work is not silently killed, but the memory, file-size, and nproc caps still apply.

---

## Residual risks (local runner)

Even with all hardening applied, `LocalSubprocessRunner` retains the following unmitigated risks. These are explicitly called out in the module docstring and make the local runner unsuitable for production.

### 1. Same OS user — filesystem access

The child process runs as the same OS user as the web server. It can read any file that user can read: application source, `.env` files, TLS private keys, SSH keys, etc.

Env scrubbing blocks secrets from being *passed via environment variables*, but does not prevent the child from reading them directly from disk.

### 2. Host network namespace — IMDS access

The child shares the host network namespace and can reach:

- `169.254.169.254` — AWS/GCP/Azure Instance Metadata Service (IAM credentials)
- `fd00:ec2::254` — AWS IMDSv2 over IPv6
- Any RFC-1918 address reachable from the host

An attacker who can submit code can exfiltrate cloud IAM credentials from IMDS or pivot to internal services.

Required egress firewall rules for any deployment that uses the local runner:

```
# Block IMDS
169.254.0.0/16   DENY  (link-local / all cloud providers)
fd00:ec2::/32    DENY  (AWS IPv6 IMDS)

# Block RFC-1918
10.0.0.0/8       DENY
172.16.0.0/12    DENY
192.168.0.0/16   DENY
```

### 3. `RLIMIT_NPROC` is per-OS-user, not per-subtree

`RLIMIT_NPROC` limits processes owned by the OS user, not just the subprocess subtree. A fork bomb can transiently exhaust kernel thread slots before the limit fires, potentially affecting the web server.

### 4. No cgroup or namespace isolation

No CPU cgroup, memory cgroup, PID namespace, network namespace, or filesystem namespace (chroot/pivot_root). The child has full visibility of the host process table (`/proc`), network interfaces, and mounted filesystems.

---

## Production checklist

The only production-safe path is a remote sandboxed runner combined with egress firewall rules.

1. **Configure E2B** (recommended):

   ```bash
   KERNEL_REMOTE_PROVIDER=e2b
   E2B_API_KEY=<your-e2b-api-key>
   ```

2. **Set `ENV=production`**:

   ```bash
   ENV=production
   ```

   With this set and no remote runner, any kernel request returns 503 `kernel_disabled` — there is no silent fallback to the local runner.

3. **Apply egress firewall rules** blocking link-local and RFC-1918 ranges (see above). This is defence-in-depth even with E2B, since the Nubi server process itself must not be reachable from a compromised request.

---

## Development / test usage

`LocalSubprocessRunner` is safe for local development when:

- The machine is not a cloud VM with an IMDS endpoint.
- No production secrets are present on the filesystem.
- `ENV` is `development` or `test`.

```bash
ENV=development
KERNEL_LOCAL_ENABLED=true   # default; explicit for clarity
```

The test suite sets `ENV=test` automatically via `conftest.py`.

---

## Related docs

- [Connector security](/docs/connector-security) — AES-256-GCM secret encryption, key rotation, network modes
- [Embedding & JWT trust boundary](/docs/embedding) — RS256/ES256 tokens, RLS policy injection
- [Secrets](/docs/secrets) — `{{ secrets.NAME }}` in flows, `nubi secrets set/list`
- [Flows](/docs/flows) — cell-based flow authoring including Python cells
- [Architecture](/docs/architecture-open-core) — open-core split, EE tree, feature gates
