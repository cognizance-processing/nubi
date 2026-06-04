# Nubi Kernel Security

## Overview

Nubi supports executing user Python code in two tiers:

| Tier | Runner | Environment | Isolation |
|------|--------|-------------|-----------|
| `remote_kernel` | E2BRunner / ModalRunner | **Production (recommended)** | Firecracker microVM / container — no host access |
| `local_kernel` | LocalSubprocessRunner | **Development only** | Same OS user, host network exposed |

---

## Production path: E2B remote sandbox (recommended)

### What E2B provides

E2B runs each sandbox in a **Firecracker microVM** — a lightweight virtualisation technology also used by AWS Lambda.  The isolation properties are:

* **No host filesystem access**: the VM has its own ephemeral root filesystem.  Nubi's credentials, secrets, and application files are invisible.
* **No host network/IMDS access**: the VM network namespace is isolated.  The AWS Instance Metadata Service (`169.254.169.254`, `fd00:ec2::254`) and all RFC-1918 addresses on the Nubi host are unreachable from inside the VM.  An attacker cannot exfiltrate cloud IAM credentials via IMDS.
* **No host process visibility**: the VM runs a separate kernel; user code cannot `ptrace`, `kill`, or `ps` the Nubi web server.
* **Ephemeral**: each request creates a fresh sandbox and kills it after completion.  There is no state leakage between executions.

### Enabling E2B

1. Install the SDK (optional — lazy-imported at runtime):

   ```
   pip install e2b-code-interpreter
   ```

2. Set environment variables:

   ```
   KERNEL_REMOTE_PROVIDER=e2b
   E2B_API_KEY=e2b-...
   ```

3. The runner is automatically selected in **all environments**, including production:

   ```
   # Development
   ENV=development
   KERNEL_REMOTE_PROVIDER=e2b
   E2B_API_KEY=e2b-...

   # Production (same config — remote runner is always preferred)
   ENV=production
   KERNEL_REMOTE_PROVIDER=e2b
   E2B_API_KEY=e2b-...
   ```

### E2B SDK methods used (verified from e2b_code_interpreter v2.7.0)

| Operation | Method |
|-----------|--------|
| Create sandbox | `Sandbox.create(api_key=..., timeout=...)` |
| Write input file | `sbx.files.write(path: str, data: bytes)` |
| Read output file | `sbx.files.read(path: str, format='bytes') -> bytes` |
| Run harness code | `sbx.run_code(code: str, timeout: float) -> Execution` |
| Stdout output | `execution.logs.stdout: list[str]` |
| Execution error | `execution.error: ExecutionError \| None` |
| Kill sandbox | `sbx.kill()` |

---

## Alternative: Modal remote sandbox

Modal (https://modal.com) provides serverless containers with similar isolation.

```
KERNEL_REMOTE_PROVIDER=modal
MODAL_TOKEN_ID=...
MODAL_TOKEN_SECRET=...
pip install modal
```

**Note**: E2B is the primary tested path.  Modal support is available as an adapter but the execution body is not yet fully implemented (raises 503 with a descriptive message).

---

## Development path: LocalSubprocessRunner

**⚠ WARNING: dev-grade isolation only.  Never use in production.**

The `LocalSubprocessRunner` executes code in a Python subprocess on the same host as the Nubi web server.  Residual risks:

* **Same OS user**: the child process can read any file the web server can read (modulo env scrubbing — secrets are not passed via env vars).
* **Host network access**: the subprocess shares the host network namespace and can reach the AWS IMDS at `169.254.169.254` and any RFC-1918 address.
* **No cgroup isolation**: `rlimit` limits are per-process; a fork-bomb may exhaust kernel thread limits before `RLIMIT_NPROC` kicks in.

M4-SEC hardening applied to the local runner:
- Process-group kill (`os.killpg`) on timeout — kills orphan grandchildren.
- `rlimit` caps: CPU time, address space (2 GiB), file size (128 MiB), NPROC (64).
- Stdout/stderr capped at 1 MiB each.
- Arrow IPC output capped at 64 MiB.
- Env scrubbing: `DATABASE_URL`, `JWT_SECRET`, `GOOGLE_*`, `AWS_*`, `AZURE_*`, `GCP_*`, `OPENAI_*`, `ANTHROPIC_*` are never passed to the child.

### Enabling local runner (development only)

```
ENV=development
KERNEL_LOCAL_ENABLED=true
# Leave KERNEL_REMOTE_PROVIDER unset or empty
```

The local runner is **automatically blocked in production** (`ENV=production`) regardless of `KERNEL_LOCAL_ENABLED`.

---

## Runner selection logic

```
if KERNEL_REMOTE_PROVIDER == 'e2b' and E2B_API_KEY:
    → E2BRunner  (any env, including production)
elif KERNEL_REMOTE_PROVIDER == 'modal' and MODAL_TOKEN_ID and MODAL_TOKEN_SECRET:
    → ModalRunner  (any env, including production)
elif ENV != 'production' and KERNEL_LOCAL_ENABLED:
    → LocalSubprocessRunner  (dev/test only)
else:
    → 503 kernel_disabled
```

---

## Security gates (always enforced — independent of runner)

| Gate | Behaviour |
|------|-----------|
| Embed token (`kind != 'access'`) | 403 forbidden — embed tokens cannot execute code |
| Missing `exec:kernel` scope | 403 forbidden — `edit:*` or `*` imply this scope |
| Code length > 100,000 chars | 413 code_too_large |
| Output > 64 MiB | 413 kernel_output_too_large |
| Timeout | 504 kernel_timeout |

---

## Summary: production deployment checklist

- [ ] Set `KERNEL_REMOTE_PROVIDER=e2b` and `E2B_API_KEY=<your-key>`.
- [ ] `pip install e2b-code-interpreter` (or add to requirements).
- [ ] Set `ENV=production` (ensures `LocalSubprocessRunner` can never be used as fallback).
- [ ] Confirm `X-Nubi-Tier: remote_kernel` appears in responses (not `local_kernel`).
- [ ] Confirm embed tokens are rejected with 403 from the `/compute/run` endpoint.
