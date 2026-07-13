#!/usr/bin/env python3
"""Run the Nubi backend.

    ./run.py                 # start the API on http://0.0.0.0:8000 with --reload
    ./run.py --port 9000     # different port
    ./run.py --no-reload     # disable autoreload (production-ish)
    ./run.py --worker        # run the flows worker (worker.py) instead
    ./run.py --no-install    # skip the dependency install step (assume deps present)

On first run this creates a virtualenv at <repo-root>/.venv (Python 3.13, the
version the project targets — see the Dockerfiles) and installs
requirements.txt into it, then re-executes itself inside that venv.

Prefers `uv` if it is on PATH (fetches Python 3.13 automatically, no apt/sudo);
otherwise falls back to the stdlib `venv` module.

Configuration is read from a .env file at the repo root (see .env.example).
Works no matter what directory you invoke it from.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
PYTHON_VERSION = "3.13"  # matches backend/Dockerfile (python:3.13-slim)


def _venv_python() -> Path:
    bindir = "Scripts" if os.name == "nt" else "bin"
    return VENV / bindir / ("python.exe" if os.name == "nt" else "python")


def _ensure_venv(install: bool) -> Path:
    """Create <root>/.venv if needed and install requirements. Returns its python."""
    py = _venv_python()
    uv = shutil.which("uv")

    if not py.exists():
        if uv:
            print(f"==> Creating virtualenv at {VENV} (uv, Python {PYTHON_VERSION})")
            subprocess.check_call([uv, "venv", "--python", PYTHON_VERSION, str(VENV)])
        else:
            print(f"==> Creating virtualenv at {VENV} ({sys.executable})")
            subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])

    if install:
        print("==> Installing backend requirements (this may take a minute)…")
        if uv:
            subprocess.check_call(
                [uv, "pip", "install", "--python", str(py), "-r", str(REQUIREMENTS)]
            )
        else:
            subprocess.check_call([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
            subprocess.check_call([str(py), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)])
    return py


def _reexec_in_venv(py: Path) -> None:
    """Re-run this script with the venv interpreter so imports resolve."""
    env = dict(os.environ, NUBI_RUN_BOOTSTRAPPED="1")
    os.execve(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]], env)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Nubi backend")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", dest="reload", action="store_false")
    parser.add_argument("--no-install", dest="install", action="store_false",
                        help="skip dependency install")
    parser.add_argument("--worker", action="store_true",
                        help="run the flows worker instead of the API server")
    args = parser.parse_args()

    # Bootstrap into the venv on first pass, then re-exec ourselves inside it.
    if not os.environ.get("NUBI_RUN_BOOTSTRAPPED"):
        py = _ensure_venv(install=args.install)
        _reexec_in_venv(py)  # does not return

    if not (ROOT / ".env").exists():
        print("!!  No .env found at repo root — copy .env.example to .env first.\n"
              "    cp .env.example .env", file=sys.stderr)

    if args.worker:
        cmd = [sys.executable, "worker.py"]
    else:
        cmd = [sys.executable, "-m", "uvicorn", "main:app",
               "--host", args.host, "--port", str(args.port)]
        if args.reload:
            cmd.append("--reload")

    print(f"==> {'worker' if args.worker else f'API on http://{args.host}:{args.port}'}")
    return subprocess.call(cmd, cwd=str(BACKEND))


if __name__ == "__main__":
    raise SystemExit(main())
