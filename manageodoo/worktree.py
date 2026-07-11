"""Lockstep git-worktree orchestration.

Odoo is two repos (community + enterprise) that must stay on matching branches.
``add`` creates a worktree on the same branch in each, transactionally (rolling
back community if enterprise fails), then returns a ready-to-register
Environment pointing at the pair.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config, detect, gitutil, ports
from .env import Environment
from .errors import EnvExists, ManageodooError


def slug(branch: str) -> str:
    """Filesystem/DB-safe name from a branch (keeps dots/dashes)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", branch.strip()).strip("_") or "wt"


def _add_one(repo: str, path: Path, branch: str, start_point: str | None) -> None:
    """Create one worktree: check out the branch if it exists locally, else
    track a matching remote branch, else create it off start_point (or HEAD)."""
    if gitutil.local_branch_exists(repo, branch):
        gitutil.worktree_add(repo, str(path), branch=branch)
        return
    remote = gitutil.remote_branch_ref(repo, branch)
    if remote:
        gitutil.worktree_add(repo, str(path), new_branch=branch,
                             start_point=remote, track=True)
    else:
        gitutil.worktree_add(repo, str(path), new_branch=branch,
                             start_point=start_point or "HEAD")


def _default_parent(doc: dict, source: Environment) -> Path:
    """Resolve the parent dir new worktrees go under. Honors a configured
    ``worktrees_dir`` default (relative paths resolve against the source env's
    install root); falls back to the built-in central location."""
    wt_root = str(config.defaults(doc).get("worktrees_dir") or "")
    if wt_root:
        p = Path(wt_root).expanduser()
        if not p.is_absolute():
            p = (Path(source.root) / p).resolve()
        return p
    return config.worktrees_dir()


def add(doc: dict, source: Environment, branch: str, *, name: str | None = None,
        path: str | None = None, start_point: str | None = None,
        fetch: bool = False, http_port: int | None = None,
        repos: str = "both") -> Environment:
    """Create worktrees for the requested repos (``both``/``community``/
    ``enterprise``) and return the Environment to register. Transactional:
    leaves no half-created worktree behind.

    ``enterprise`` mode worktrees only the enterprise repo and reuses the source
    env's community to stay runnable."""
    wt_name = name or slug(branch)
    if wt_name in doc.get("env", {}):
        raise EnvExists(f"Environment '{wt_name}' already exists.")

    comm_origin = source.community
    ent_origin = source.enterprise
    want_comm = repos in ("both", "community")
    want_ent = repos in ("both", "enterprise") and bool(ent_origin)
    if repos == "enterprise" and not ent_origin:
        raise ManageodooError(
            f"Source env '{source.name}' has no enterprise repo to worktree.")

    if want_comm and (not comm_origin or not gitutil.is_repo(comm_origin)):
        raise ManageodooError(
            f"Source env '{source.name}' community is not a git repo "
            f"({comm_origin or 'unset'}); cannot create a worktree.")
    if want_ent and not gitutil.is_repo(ent_origin):
        raise ManageodooError(f"Enterprise dir is not a git repo ({ent_origin}).")
    if not want_comm and not comm_origin:
        raise ManageodooError(
            "enterprise-only worktree needs the source env to have a community "
            "path to run against.")

    parent = Path(path).expanduser() if path else _default_parent(doc, source)
    base = parent / wt_name
    if base.exists() and any(base.iterdir()):
        raise ManageodooError(f"Target dir exists and is not empty: {base}")
    comm_path = base / "community"
    ent_path = base / "enterprise"

    if fetch:
        if want_comm:
            gitutil.fetch(comm_origin)
        if want_ent:
            gitutil.fetch(ent_origin)

    added: list[tuple[str, Path]] = []
    try:
        if want_comm:
            at = gitutil.branch_checked_out_at(comm_origin, branch)
            if at:
                raise ManageodooError(
                    f"Branch '{branch}' is already checked out at {at} "
                    "(community). Use a different branch name.")
            _add_one(comm_origin, comm_path, branch, start_point)
            added.append((comm_origin, comm_path))
        if want_ent:
            at2 = gitutil.branch_checked_out_at(ent_origin, branch)
            if at2:
                raise ManageodooError(
                    f"Branch '{branch}' is already checked out at {at2} "
                    "(enterprise).")
            _add_one(ent_origin, ent_path, branch, start_point)
            added.append((ent_origin, ent_path))
    except Exception:
        for repo, p in reversed(added):  # roll back so repos never drift
            try:
                gitutil.worktree_remove(repo, str(p), force=True)
            except Exception:
                pass
        if base.is_dir() and not any(base.iterdir()):
            try:
                base.rmdir()
            except OSError:
                pass
        raise

    # Resolve the env's source paths depending on which repos were worktreed.
    env_community = str(comm_path) if want_comm else source.community
    env_enterprise = str(ent_path) if want_ent else ""
    version = detect._read_version(Path(env_community))

    defaults = config.defaults(doc)
    if http_port is not None:
        http, gevent = http_port, ports.gevent_for(http_port, defaults)
    else:
        http, gevent = ports.allocate(doc, defaults)

    return Environment(
        name=wt_name,
        root=str(base),
        odoo_bin=str(Path(env_community) / "odoo-bin"),
        community=env_community,
        python=source.python,               # reuse the source env's venv
        enterprise=env_enterprise,
        custom_addons=list(source.custom_addons),
        version=version,
        branch=branch if want_comm else source.branch,
        enterprise_branch=branch if want_ent else "",
        database=wt_name,
        db_filter=f"^{wt_name}$",
        http_port=http,
        gevent_port=gevent,
        data_dir=source.data_dir,
        dev=source.dev,
        demo=source.demo,
        conf=str(config.conf_path(wt_name)),
        managed_worktree=True,
        wt_community_origin=comm_origin if want_comm else "",
        wt_enterprise_origin=ent_origin if want_ent else "",
    )


def _prune_empty_up(leaf: Path, *, levels: int) -> None:
    """Delete ``leaf`` and up to ``levels`` empty parents, stopping at the first
    non-empty dir. Only ever removes empty dirs (``rmdir`` errors otherwise), and
    the climb is bounded, so real repos and the install root are never touched.

    For the default layout this clears ``worktrees/<name>`` -> ``worktrees/`` ->
    ``manageodoo/`` when the last worktree goes, but leaves ``worktrees/`` alone
    while sibling worktrees remain (it isn't empty)."""
    d = leaf
    for _ in range(levels + 1):
        if not d.is_dir() or any(d.iterdir()):
            break
        parent = d.parent
        try:
            d.rmdir()
        except OSError:
            break
        d = parent


def _remove_worktree(origin: str, path: str, *, force: bool) -> None:
    """Remove one git worktree, tolerating an already-gone working dir. If the
    dir is missing (orphan — deleted out from under the registry) we skip the
    ``worktree remove`` (which would fail with 'is not a working tree') and just
    prune the stale admin entry. A present-but-dirty worktree still raises so the
    user learns to pass --force."""
    if Path(path).exists():
        gitutil.worktree_remove(origin, path, force=force)
    gitutil.worktree_prune(origin)


def remove(env: Environment, *, force: bool = False) -> None:
    """Remove the worktrees this env actually created (guarded by the stored
    origins, so a reused source community is never touched), prune, and clean up
    the now-empty base dir and any empty scaffolding above it. (The branch git
    created is left in place, as git does.)"""
    if env.wt_community_origin:
        _remove_worktree(env.wt_community_origin, env.community, force=force)
    if env.wt_enterprise_origin:
        _remove_worktree(env.wt_enterprise_origin, env.enterprise, force=force)
    # git removed the community/ and enterprise/ subdirs; drop the empty base and
    # empty parents (worktrees/, manageodoo/) so no empty scaffolding is left.
    _prune_empty_up(Path(env.root), levels=2)
