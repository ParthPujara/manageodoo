"""Thin PostgreSQL helpers — shell out to psql/dropdb, mirroring what a
developer does by hand (``dropdb <name>``). No Python DB driver dependency."""

from __future__ import annotations

import shutil
import subprocess

from .errors import ManageodooError


def dropdb_available() -> bool:
    return shutil.which("dropdb") is not None


def database_exists(name: str) -> bool:
    """True if a database with this name exists (best-effort; False if psql
    is unavailable or the query fails)."""
    if shutil.which("psql") is None:
        return False
    quoted = name.replace("'", "''")
    query = f"SELECT 1 FROM pg_database WHERE datname = '{quoted}'"
    try:
        # Connect to the always-present 'postgres' maintenance DB; without -d,
        # psql would try a DB named after the current user and fail.
        out = subprocess.run(
            ["psql", "-d", "postgres", "-XtAc", query],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "1"


def drop_database(name: str, force: bool = False) -> None:
    """Run ``dropdb --if-exists [--force] <name>``; raise on failure."""
    if not dropdb_available():
        raise ManageodooError("dropdb not found on PATH; cannot drop database.")
    args = ["dropdb", "--if-exists"]
    if force:
        args.append("--force")  # terminate active connections (PostgreSQL 13+)
    args.append(name)
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManageodooError(f"dropdb failed: {exc}") from exc
    if out.returncode != 0:
        msg = out.stderr.strip() or out.stdout.strip() or "unknown error"
        raise ManageodooError(f"dropdb failed: {msg}")
