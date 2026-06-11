"""Minimal setup.py so the package is pip-installable as `pip install -e .`."""

from setuptools import find_packages, setup

setup(
    name="nubi-cli",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "typer>=0.12.0",
        "httpx>=0.27.0",
        "rich>=13.0.0",
        "pyyaml>=6.0",
        "toml>=0.10.2",
    ],
    extras_require={
        "arrow": ["pyarrow>=14.0.0"],
        # PyNaCl is only needed to seal GitHub Actions secrets; the CLI degrades
        # with a clear error when it is absent (doc C).
        "secrets": ["pynacl>=1.5.0"],
        # The bridge agent dials out over WebSocket (design §7). websockets is
        # imported lazily inside `nubi bridge start`, so the core CLI install
        # stays lean — `pip install nubi[bridge]` pulls it for agent hosts.
        "bridge": ["websockets>=12.0"],
    },
    entry_points={
        "console_scripts": [
            "nubi=nubi_cli.main:main",
        ],
    },
    python_requires=">=3.11",
)
