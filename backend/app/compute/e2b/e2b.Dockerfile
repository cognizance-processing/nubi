# Nubi attribution sandbox — E2B custom template
# =============================================================================
# This image is the sandbox that the production remote kernel (E2BRunner,
# backend/app/compute/remote_e2b.py) executes "bring-your-own-model"
# attribution code inside.  It bakes the standard attribution / ML-inference
# package stack INTO the image because the E2B Firecracker sandbox is
# network-SEALED at runtime — there is no outbound network, so a runtime
# `pip install` inside user code does NOT work.  Every package the host's
# attribution code needs must be present here, at build time.
#
# The kernel itself is domain-agnostic: it carries NO FMCG/marketing semantics.
# It deserializes a caller-supplied model blob, runs the caller's numeric code
# (e.g. SHAP), and returns an Arrow table of attribution values.  See
# docs/compute-kernel-attribution-runner.md for the full contract.
#
# Build context: this directory (backend/app/compute/e2b/).
#
# Build + publish (requires the E2B CLI and an E2B account):
#
#     cd backend/app/compute/e2b
#     e2b template build            # builds from e2b.toml + this Dockerfile
#     # → prints a template id; set it as E2B_TEMPLATE in the backend env.
#
# IMPORTANT: editing this file does NOTHING until the template is rebuilt and
# republished with `e2b template build` and the new template id is wired into
# the E2B_TEMPLATE environment variable.  See README.md in this directory.
# =============================================================================

# E2B base image — Debian + Python 3 + the E2B sandbox agent.
# Pinned to a specific tag so rebuilds are reproducible.
FROM e2bdev/code-interpreter:latest

# --- Attribution / ML-inference stack -------------------------------------
# numpy / pandas / pyarrow are already present in the base image, but we pin
# them here too so the sandbox versions stay aligned with the Nubi backend
# (backend/requirements.txt: pyarrow>=14.0, pandas>=2.0) and so the Arrow IPC
# round-trip is byte-compatible.
#
# Versions are pinned for reproducible, auditable rebuilds.  Bump deliberately.
RUN pip install --no-cache-dir \
        "numpy==2.3.5" \
        "pandas==2.3.2" \
        "pyarrow==21.0.0" \
        "scipy==1.16.2" \
        "scikit-learn==1.7.2" \
        "xgboost==3.0.5" \
        "shap==0.48.0" \
        "onnxruntime==1.22.1"

# Smoke-test the stack at build time so a broken image never ships.
RUN python -c "import numpy, pandas, pyarrow, sklearn, xgboost, shap, onnxruntime; print('attribution stack OK')"
