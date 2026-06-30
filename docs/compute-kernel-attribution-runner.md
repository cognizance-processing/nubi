# Compute kernel — bring-your-own-model attribution runner

Nubi's compute kernel is a **generic, sandboxed numeric runner**. This document
specifies how to use it as a supported, **domain-agnostic
bring-your-own-model (BYO-model) attribution runner**: you submit Python source
plus named Arrow tables (your feature matrix and a serialized model), the kernel
runs your code inside an isolated sandbox, and returns an Arrow table of
attribution values (e.g. SHAP values).

> **Boundary statement.** The kernel is a numeric primitive. It carries **no
> domain, FMCG, or marketing semantics**. Nubi **executes the host's numeric
> code**, but it **never stores the model and never interprets the attribution
> values**. The model blob is passed in, used in-sandbox, and discarded with the
> sandbox; the output table is handed back verbatim. This keeps Nubi firmly on
> the "no ML modeling" side of the line — you bring the model and the math, Nubi
> brings the sandboxed compute.

## The contract

| Aspect      | Specification                                                              |
| ----------- | ------------------------------------------------------------------------- |
| Language    | Python only.                                                              |
| Input       | Python source string + named Arrow tables (`dict[str, pyarrow.Table]`).   |
| Feature data| Numeric columns of a feature matrix, as an Arrow table.                   |
| Model       | A serialized model **blob**, passed as a single-row `pa.binary()` column. |
| Output      | The user code MUST assign an Arrow table / pandas DataFrame to `result`.  |
| `result`    | An Arrow table of SHAP / feature-attribution values.                      |
| Injected    | `pa` (alias for `pyarrow`) and `inputs` (the named-table dict).           |

### Resource limits

These limits apply to the production (E2B) runner. They are hard ceilings —
exceeding them aborts the run.

| Limit         | Value                          |
| ------------- | ------------------------------ |
| Runtime       | Python only (no shell/network) |
| Wall-clock    | 120 s                          |
| Memory        | 2 GiB                          |
| Result size   | 64 MiB (Arrow IPC)             |
| Code size     | ~100k characters               |
| Network       | None — the sandbox is sealed   |

Because the sandbox is **network-sealed**, a runtime `pip install` does **not**
work. The attribution / ML-inference stack (`shap`, `scikit-learn`,
`onnxruntime`, `xgboost`, plus `numpy` / `pandas` / `pyarrow`) is **baked into
the sandbox image** at build time. That image is defined in-repo at
`backend/app/compute/e2b/` (`e2b.Dockerfile` + `e2b.toml`); after building and
publishing it with the E2B CLI, wire its template id into the `E2B_TEMPLATE`
environment variable. See `backend/app/compute/e2b/README.md`.

### How a model is passed in

The kernel transports everything as Arrow. A model is therefore passed as a
**binary column**: serialize your model to bytes (pickle, joblib, ONNX,
XGBoost's native format, …), put those bytes in a one-row `pa.binary()` column,
and submit it as one of the named input tables. Inside the sandbox your code
reads the bytes back out of `inputs[...]` and deserializes them.

Nubi does not parse, validate, or persist the blob. It is opaque bytes that flow
in with the request and vanish with the sandbox.

## Worked example (copy-pasteable)

The caller builds two Arrow tables — a numeric feature matrix and a one-row
binary column holding the serialized model — and submits Python that
deserializes the model, runs SHAP, and assigns the SHAP values to `result`.

### Caller side (host) — build the inputs and submit

```python
import pickle

import numpy as np
import pyarrow as pa
from sklearn.ensemble import GradientBoostingRegressor

# 1. Train (or load) your model on the HOST. Nubi never sees how this happens.
X = np.random.default_rng(0).normal(size=(200, 4))
y = X @ np.array([1.5, -2.0, 0.0, 0.7]) + 0.1
model = GradientBoostingRegressor().fit(X, y)

# 2. Serialize the model to bytes and wrap it in a one-row pa.binary() column.
model_bytes = pickle.dumps(model)
model_table = pa.table({"blob": pa.array([model_bytes], type=pa.binary())})

# 3. Build the feature matrix you want attributions for, as a numeric table.
feature_table = pa.table(
    {f"f{i}": pa.array(X[:, i], type=pa.float64()) for i in range(X.shape[1])}
)

# 4. The Python the sandbox will execute (deserialize → SHAP → result).
code = r"""
import pickle
import numpy as np
import shap

# Pull the serialized model out of the binary column and deserialize it.
model = pickle.loads(inputs["model"]["blob"][0].as_py())

# Reconstruct the numeric feature matrix from the Arrow table.
feat = inputs["features"]
cols = feat.column_names
X = np.column_stack([feat[c].to_numpy(zero_copy_only=False) for c in cols])

# Run the caller's attribution math (SHAP). Nubi does not interpret these.
explainer = shap.Explainer(model)
shap_values = explainer(X).values  # shape: (n_rows, n_features)

# Hand back an Arrow table of attribution values, one column per feature.
result = pa.table(
    {f"shap_{c}": shap_values[:, i] for i, c in enumerate(cols)}
)
"""

# 5. Submit to the kernel (named tables: "features" and "model").
#    e.g. via app.compute.kernel_interface.get_kernel_runner("remote_kernel")
#    runner.run(code=code,
#               inputs={"features": feature_table, "model": model_table},
#               timeout_s=120)
```

### What comes back

`KernelResult.table` is an Arrow table with one column of SHAP values per input
feature (`shap_f0`, `shap_f1`, …), one row per input row. Nubi returns it
verbatim; interpreting those numbers is the caller's job.

## Notes

- The local subprocess runner (`LocalSubprocessRunner`) follows the **same
  contract** and is useful for development and CI with lightweight, pure-numpy
  attributions, but it is **dev-grade isolation only**. Production must use the
  remote (E2B) runner. See `docs/kernel-security.md`.
- `result` may be a `pyarrow.Table` or a `pandas.DataFrame` (auto-converted).
- Keep the result under the 64 MiB Arrow cap — return attribution values, not
  raw intermediate arrays.
