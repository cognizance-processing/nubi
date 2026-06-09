# Kernel Security — Residual Risk & Production Requirements

> **M4-SEC hardening applied.** This document describes what the hardening
> covers, what residual risk remains, and what MUST be done before production.

---

## What the M4-SEC hardening does

| Layer | Fix | Status |
|---|---|---|
| **Production guard** | `ENV == 'production'` → local runner blocked; 503 unless remote runner configured | Applied |
| **exec:kernel scope** | Route requires `exec:kernel` (implied by `edit:*` / `*`) | Applied |
| **Code length cap** | Code > 100,000 chars → 413 before subprocess launch | Applied |
| **Timeout cap** | `timeout_s` clamped to 120 s | Confirmed (pre-existing) |
| **Process-group kill** | `start_new_session=True` + `os.killpg(SIGKILL)` on timeout | Applied |
| **rlimits (POSIX)** | `RLIMIT_CPU`, `RLIMIT_AS` (2 GiB), `RLIMIT_FSIZE` (128 MiB), `RLIMIT_NPROC` (64) | Applied |
| **Output caps** | stdout/stderr truncated to 1 MiB each before decode | Applied |
| **Temp cleanup** | `try/finally shutil.rmtree` on all code paths | Applied (pre-existing, verified) |
| **Env scrubbing** | Empty env; only PATH, PYTHONPATH, HOME, TMPDIR/TEMP/TMP, LANG/LC_ALL/LC_CTYPE, VIRTUAL_ENV forwarded | Applied (pre-existing) |

> Flows Python cells (`kind='python'`) share the same hardened subprocess
> helper (`app/compute/sandbox.py`): process-group kill on timeout, rlimits
> (RLIMIT_CPU is skipped when a cell sets `timeout_s=0`, i.e. no timeout),
> and 1 MiB stdout/stderr caps. Limits are overridable via `KERNEL_RLIMIT_*`
> / `KERNEL_STDOUT_CAP_BYTES` / `KERNEL_STDERR_CAP_BYTES` env vars.

---

## Residual risk — LOCAL SUBPROCESS IS NOT A SANDBOX

Even with all M4-SEC hardening applied, `LocalSubprocessRunner` retains the
following **unmitigated risks**:

### 1. Same OS user — file system access
The child process runs as the **same OS user as the web server**.  It can read
any file the OS user can read: application source code, `.env` files,
configuration, TLS private keys, SSH keys, etc.

Env scrubbing prevents secrets from being passed via environment variables, but
does NOT prevent the child from reading them directly from disk.

### 2. Host network namespace — IMDS access
The child shares the **host network namespace** and can reach:

- `169.254.169.254` (AWS/GCP/Azure Instance Metadata Service — IAM credentials)
- `fd00:ec2::254` (AWS IMDSv2 over IPv6)
- Any RFC-1918 address reachable from the host (internal APIs, databases, etc.)

An attacker who can submit code can exfiltrate cloud IAM credentials from the
IMDS, pivot to other internal services, or exfiltrate data over the network.

**Required egress firewall rules** before going to production with any kernel:

```
# Block AWS/GCP/Azure IMDS
169.254.0.0/16   DENY (link-local)
fd00:ec2::/32    DENY (AWS IPv6 IMDS)

# Block RFC-1918 (internal networks)
10.0.0.0/8       DENY
172.16.0.0/12    DENY
192.168.0.0/16   DENY
```

### 3. rlimits are per-OS-user, not per-subtree
`RLIMIT_NPROC` limits the number of processes owned by the OS user, not just
the subprocess subtree.  A fork bomb can still transiently exhaust kernel
thread/process slots before the limit fires, potentially affecting the web
server process.

### 4. No cgroup or namespace isolation
There is no CPU cgroup, memory cgroup, PID namespace, network namespace, or
filesystem namespace (chroot/pivot_root).  The child has full visibility of the
host process table (`/proc`), network interfaces, and mounted filesystems.

---

## Production requirements

**The ONLY production-safe path is a remote sandboxed runner (E2B or Modal)**
combined with egress firewall rules.

1. **Configure a remote runner** by setting the appropriate env vars:

   - **E2B** (Firecracker microVMs — recommended):
     ```
     KERNEL_REMOTE_PROVIDER=e2b
     E2B_API_KEY=<your-e2b-api-key>
     ```
   - **Modal** (container-based):
     ```
     KERNEL_REMOTE_PROVIDER=modal
     MODAL_TOKEN_ID=<your-modal-token-id>
     MODAL_TOKEN_SECRET=<your-modal-token-secret>
     ```

   Remote runners take precedence over the local runner in all environments,
   including production.  E2B sandboxes run in isolated Firecracker microVMs
   with no access to the host filesystem, network, IMDS, or secrets.

2. **Apply egress firewall rules** blocking link-local and RFC-1918 ranges
   (see above).

3. **Set `ENV=production`**:
   ```
   ENV=production
   ```
   When `ENV=production` and no remote runner is configured, `_choose_runner()`
   raises `AppError("kernel_disabled", 503)` — the local subprocess runner is
   never used, preventing any silent fallback.  Setting `KERNEL_LOCAL_ENABLED`
   is not required (it is already ignored in production), but you may set it to
   `false` for defence-in-depth.

   The runner-selection priority in `app/routes/compute.py` is:
   1. `KERNEL_REMOTE_PROVIDER=e2b` + `E2B_API_KEY` → E2BRunner (any env).
   2. `KERNEL_REMOTE_PROVIDER=modal` + Modal credentials → ModalRunner (any env).
   3. `ENV != production` + `KERNEL_LOCAL_ENABLED=true` → LocalSubprocessRunner.
   4. Otherwise → 503 `kernel_disabled`.

---

## Development / test usage

`LocalSubprocessRunner` is safe for local development where:
- The machine is not a cloud VM with IMDS.
- No production secrets are present on the filesystem.
- `ENV` is set to `development` or `test` (default in `.env.example`).

Set `KERNEL_LOCAL_ENABLED=true` (the default) and `ENV=development` to use the
local runner in dev.  The test suite sets `ENV=test` automatically via
`conftest.py`.

---

## References

- [AWS IMDS security best practices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-retrieval.html)
- [gVisor runsc](https://gvisor.dev/docs/user_guide/quick_start/oci/)
- [E2B sandboxed code execution](https://e2b.dev/docs)
- [Modal sandboxes](https://modal.com/docs/guide/sandbox)
- POSIX `setrlimit(2)` man page
