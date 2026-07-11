"""Registry storage: XDG paths plus TOML load/save.

The registry is a single TOML file holding a ``[defaults]`` table and one
``[env.<name>]`` table per environment. Reads use stdlib ``tomllib`` on 3.11+
and fall back to ``tomli``; writes always go through ``tomli_w`` (no stdlib
writer exists).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib as _toml_read
except ModuleNotFoundError:  # 3.9 / 3.10
    import tomli as _toml_read  # type: ignore[no-redefine]

import tomli_w

# Built-in defaults, overlaid by the registry's own ``[defaults]`` table.
DEFAULTS: dict[str, Any] = {
    "http_port_base": 8069,
    "port_stride": 10,
    "gevent_offset": 3,
    "data_dir": "~/.local/share/Odoo",
    "dev": "all",
    "demo": False,
    # Default parent dir for `worktree add`, resolved against each source env's
    # install root when relative — so worktrees land inside the install root but
    # outside the community/enterprise repos, as
    # manageodoo/worktrees/<name>/{community,enterprise}. Set an absolute path to
    # override, or empty to fall back to ~/.local/share/manageodoo/worktrees.
    "worktrees_dir": "manageodoo/worktrees",
}


def config_home() -> Path:
    """Base config dir, honoring ``$XDG_CONFIG_HOME``."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "manageodoo"


def config_path() -> Path:
    return config_home() / "config.toml"


def envs_dir() -> Path:
    return config_home() / "envs"


def conf_path(name: str) -> Path:
    """Path of the generated Odoo ``-c`` INI file for an environment."""
    return envs_dir() / f"{name}.conf"


def data_home() -> Path:
    """Base data dir, honoring ``$XDG_DATA_HOME`` (default ~/.local/share)."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "manageodoo"


def worktrees_dir() -> Path:
    """Where managed git worktrees are created."""
    return data_home() / "worktrees"


def load() -> dict[str, Any]:
    """Load the whole registry document (empty skeleton if none yet)."""
    path = config_path()
    if not path.exists():
        return {"version": 1, "defaults": {}, "env": {}}
    with path.open("rb") as fh:
        doc = _toml_read.load(fh)
    doc.setdefault("version", 1)
    doc.setdefault("defaults", {})
    doc.setdefault("env", {})
    return doc


def save(doc: dict[str, Any]) -> None:
    """Persist the registry, creating the config dir on first write."""
    config_home().mkdir(parents=True, exist_ok=True)
    with config_path().open("wb") as fh:
        tomli_w.dump(doc, fh)


def defaults(doc: dict[str, Any]) -> dict[str, Any]:
    """Built-in defaults overlaid by the registry's ``[defaults]`` table."""
    merged = dict(DEFAULTS)
    merged.update(doc.get("defaults", {}))
    return merged
