"""Repeatable pre-release gate.

Runs the sanity checks that should pass before shipping, in one command:

* byte-compile the whole tree (catches syntax errors)
* the Postgres dialect smoke test, when DATABASE_URL is set

Run it with the repo's venv, e.g. `venv\\Scripts\\python.exe scripts/prerelease_check.py`.
The Postgres smoke test needs a live DATABASE_URL; when it is not set the check
skips it with a reminder rather than failing, so the same command is usable both
locally (SQLite) and against the deployment database.
"""

from __future__ import annotations

import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(label: str, args: list[str]) -> None:
    print(f"[prerelease] {label}: {' '.join(args)}")
    result = subprocess.run(args, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"[prerelease] {label} failed (exit {result.returncode})")


def main() -> None:
    python = sys.executable
    _run(
        "compile",
        [python, "-m", "compileall", "-q", "-x", r"venv|__pycache__", "."],
    )
    if os.getenv("DATABASE_URL"):
        _run("postgres-smoke", [python, "scripts/smoke_postgres_db.py"])
    else:
        print(
            "[prerelease] DATABASE_URL not set - skipping Postgres smoke test. "
            "Run this against the live database before shipping dialect changes."
        )
    print("[prerelease] ok")


if __name__ == "__main__":
    main()
