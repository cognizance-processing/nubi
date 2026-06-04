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
    ],
    extras_require={
        "arrow": ["pyarrow>=14.0.0"],
    },
    entry_points={
        "console_scripts": [
            "nubi=nubi_cli.main:main",
        ],
    },
    python_requires=">=3.11",
)
