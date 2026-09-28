#!/usr/bin/env python3
"""Athena Agent — the setup entry (the Operator's 08-15 portability spec).

Installation (Windows + Linux):
    python setup.py install          # system-level, deps auto-resolved
    pip install .                    # or the modern PEP-517 path

This installs the `athena` console command + declares Athena's tiny
third-party footprint (see requirements.txt). Everything else is the
Python standard library or Athena's own modules. The runtime venv is
still built separately (requirements.txt — the wipe-test keep item);
setup.py is the SYSTEM-LEVEL installation path so `athena` works as a
real command on any machine.

Windows: `python setup.py install` (or pip install .) from a shell with
Python 3.10+ on PATH. The .bat launcher creates the command shim.
Linux:   `python3 setup.py install` — the .sh launcher creates the
~/.local/bin symlink. Either path resolves the same dependencies.
"""
from pathlib import Path

from setuptools import find_packages, setup

HERE = Path(__file__).parent

# The dependency list — ONE source of truth (requirements.txt).
def _read_requirements() -> list[str]:
    reqs: list[str] = []
    try:
        for line in (HERE / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            reqs.append(line)
    except Exception:
        pass
    return reqs


setup(
    name="athena-agent",
    version="0.5.0",
    description=(
        "Athena — the self-hosted autonomous 24/7 agent. Parallel queue, "
        "workflow lanes, timeline graph, doctor, nurse, janitor."
    ),
    long_description=(HERE / ".." / ".wiki" / "Home.md").read_text(
        encoding="utf-8", errors="replace"
    )
    if (HERE / ".." / ".wiki" / "Home.md").exists()
    else "Athena Agent — the autonomous agent server.",
    long_description_content_type="text/markdown",
    author="FenrirLupus",
    url="https://github.com/FenrirLupus/Athena-Agent",
    python_requires=">=3.10",
    packages=find_packages(exclude=["tests", "tests.*", "doctor.*"]),
    include_package_data=True,
    install_requires=_read_requirements(),
    entry_points={
        "console_scripts": [
            "athena=cli.main:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
        "Topic :: System :: Monitoring",
    ],
)
