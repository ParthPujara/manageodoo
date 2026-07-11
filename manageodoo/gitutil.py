"""Thin git wrappers used by the worktree engine. Everything shells out to
``git -C <repo> …``; nothing here imports a git library."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from .errors import ManageodooError


def _git(args: list[str], *, check: bool = True, timeout: int = 120):
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManageodooError(f"git failed: {exc}") from exc
    if check and out.returncode != 0:
        msg = out.stderr.strip() or out.stdout.strip() or "unknown error"
        raise ManageodooError(f"git {' '.join(args)}: {msg}")
    return out


def is_repo(path: str) -> bool:
    return _git(["-C", str(path), "rev-parse", "--show-toplevel"],
                check=False).returncode == 0


def current_branch(path: str) -> str:
    return _git(["-C", str(path), "branch", "--show-current"],
                check=False).stdout.strip()


def local_branch_exists(repo: str, branch: str) -> bool:
    return _git(["-C", str(repo), "show-ref", "--verify", "--quiet",
                 f"refs/heads/{branch}"], check=False).returncode == 0


def remote_branch_ref(repo: str, branch: str) -> Optional[str]:
    """Return a ``<remote>/<branch>`` ref that exists, preferring the upstream
    remotes Odoo uses (origin/odoo/ent), or None."""
    out = _git(["-C", str(repo), "for-each-ref", "--format=%(refname:short)",
                "refs/remotes"], check=False)
    if out.returncode != 0:
        return None
    cands = [r for r in out.stdout.split() if r.endswith(f"/{branch}")]
    for prefix in ("origin/", "odoo/", "ent/"):
        for ref in cands:
            if ref.startswith(prefix):
                return ref
    return cands[0] if cands else None


def fetch(repo: str) -> None:
    _git(["-C", str(repo), "fetch", "--all", "--quiet"], check=False, timeout=600)


def worktree_list(repo: str) -> list[dict]:
    """Parse ``git worktree list --porcelain`` into dicts with path/head/branch."""
    out = _git(["-C", str(repo), "worktree", "list", "--porcelain"], check=False)
    result: list[dict] = []
    cur: dict = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            if cur:
                result.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].replace("refs/heads/", "")
        elif line.startswith("detached"):
            cur["branch"] = None
    if cur:
        result.append(cur)
    return result


def branch_checked_out_at(repo: str, branch: str) -> Optional[str]:
    """Path of the worktree that already has ``branch`` checked out, or None."""
    for wt in worktree_list(repo):
        if wt.get("branch") == branch:
            return wt["path"]
    return None


def worktree_add(repo: str, path: str, *, branch: Optional[str] = None,
                 new_branch: Optional[str] = None,
                 start_point: Optional[str] = None, track: bool = False) -> None:
    """`git worktree add`. Pass ``branch`` to check out an existing branch, or
    ``new_branch`` (+ optional ``start_point``/``track``) to create one."""
    args = ["-C", str(repo), "worktree", "add"]
    if track:
        args.append("--track")
    if new_branch:
        args += ["-b", new_branch]
    args.append(str(path))
    if branch:
        args.append(branch)
    elif start_point:
        args.append(start_point)
    _git(args)


def worktree_remove(repo: str, path: str, *, force: bool = False) -> None:
    args = ["-C", str(repo), "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    _git(args)


def worktree_prune(repo: str) -> None:
    _git(["-C", str(repo), "worktree", "prune"], check=False)
