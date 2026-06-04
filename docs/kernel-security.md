# Kernel Security — Residual Risk & Production Requirements

> **M4-SEC hardening applied.** This document describes what the hardening
> covers, what residual risk remains, and what MUST be done before production.

---

## What the M4-SEC hardening does

| Layer | Fix | Status |
|---|---|---|
| **Production guard** | `ENV == 'production'` + `KERNEL_LOCAL_ENABLED=false` → 503 | Applied |
| **exec:kernel scope** | Route requires `exec:kernel` (implied by `edit:*` / `*`) | Applied |
| **Code length cap** | Code > 100,000 chars → 413 before subprocess launch | Applied |
| **Timeout cap** | `timeout_s` clamped to 120 s | Confirmed (pre-existing) |
| **Process-group kill** | `start_new_session=True` + `os.killpg(SIGKILL)` on timeout | Applied |
| **rlimits (POSIX)** | `RLIMIT_CPU`, `RLIMIT_AS` (2 GiB), `RLIMIT_FSIZE` (128 MiB), `RLIMIT_NPROC` (64) | Applied |
| **Output caps** | stdout/stderr truncated to 1 MiB each before decode | Applied |
| **Temp cleanup** | `try/finally shutil.rmtree` on all code paths | Applied (pre-existing, verified) |
| **Env scrubbing** | Empty env; only PATH, PYTHONPATH, HOME, TMPDIR, LANG forwarded | Applied (pre-existing) |

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

**The ONLY production-safe path is `RemoteRunner`** combined with egress
firewall rules.

1. **Deploy a sandboxed remote runner**: Modal, E2B, gVisor (`runsc`), or a
   container with seccomp + AppArmor/SELinux profiles and a dedicated network
   namespace.

2. **Apply egress firewall rules** blocking link-local and RFC-1918 ranges
   (see above).

3. **Set environment variables**:
   ```
   ENV=production
   KERNEL_LOCAL_ENABLED=false
   ```
   This causes `_choose_runner()` to raise 503 if no remote runner is wired in,
   preventing silent fallback to the local subprocess.

4. **Wire `RemoteRunner(configured=True)`** in `app/routes/compute.py` once
   Modal/E2B credentials are available (M4-B / M4-C milestone).

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
