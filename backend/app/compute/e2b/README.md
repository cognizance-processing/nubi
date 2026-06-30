# Nubi attribution sandbox — E2B custom template

This directory defines the **custom E2B sandbox image** used by the production
remote kernel (`backend/app/compute/remote_e2b.py`, class `E2BRunner`) to run
domain-agnostic **bring-your-own-model attribution** code.

## Why this exists

The E2B Firecracker sandbox is **network-sealed** at runtime — user code has no
outbound network, so a runtime `pip install` does **not** work. Any package the
host's attribution code needs (SHAP, scikit-learn, ONNX Runtime, XGBoost, …)
must be **baked into the sandbox image at build time**. That is what
`e2b.Dockerfile` does.

Files:

- `e2b.Dockerfile` — the image definition (base image + pinned package stack).
- `e2b.toml` — the E2B CLI template manifest (name + Dockerfile path + the
  `template_id` that gets filled in after the first build).

## The package stack

On top of the base image's `numpy` / `pandas` / `pyarrow`, the template adds:

| Package        | Pinned   | Purpose                                   |
| -------------- | -------- | ----------------------------------------- |
| `scikit-learn` | 1.7.2    | Tree/linear models + utilities            |
| `xgboost`      | 3.0.5    | Gradient-boosted trees (common BYO model) |
| `shap`         | 0.48.0   | SHAP feature-attribution values           |
| `onnxruntime`  | 1.22.1   | Run exported ONNX models                  |
| `scipy`        | 1.16.2   | SHAP / sklearn dependency, pinned         |

Versions are pinned for reproducible, auditable rebuilds — bump deliberately.

## Build + publish (REQUIRED before the template takes effect)

> **TODO / IMPORTANT:** Editing `e2b.Dockerfile` or `e2b.toml` does nothing on
> its own. The image must be **rebuilt and republished**, and the resulting
> template id wired into the backend, before any of this is live.

```sh
# 1. Install the E2B CLI and log in (one-time).
npm install -g @e2b/cli
e2b auth login

# 2. Build + publish the template from this directory.
cd backend/app/compute/e2b
e2b template build          # reads e2b.toml + e2b.Dockerfile

# 3. The CLI prints a template id and writes it into e2b.toml (template_id).
#    Wire that id into the backend environment:
export E2B_TEMPLATE=<printed-template-id>
```

When `E2B_TEMPLATE` is **unset**, `E2BRunner` falls back to the E2B **base**
image (numpy / pandas / pyarrow only) — attribution imports like `shap` will
fail inside the sandbox until the custom template is built and wired in.

See `docs/compute-kernel-attribution-runner.md` for the full runner contract
and a copy-pasteable worked example.
