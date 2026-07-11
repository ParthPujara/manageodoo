"""The Environment model: one registered Odoo install.

Serializes to/from a ``[env.<name>]`` TOML table. TOML has no null, so absent
values are stored as ``""`` or omitted. ``addons_path`` is intentionally *not*
stored — it is reassembled from the source paths on every use so it survives the
dirs being moved.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Fields serialized to the registry. name is the table key, not a value.
_STR = ("root", "odoo_bin", "community", "python", "enterprise", "version",
        "branch", "enterprise_branch", "database", "db_filter", "data_dir",
        "dev", "conf", "wt_community_origin", "wt_enterprise_origin")
_LIST = ("custom_addons", "extra_args")
_INT = ("http_port", "gevent_port")
_BOOL = ("demo", "managed_worktree")


@dataclass
class Environment:
    name: str
    root: str = ""
    odoo_bin: str = ""
    community: str = ""
    python: str = ""
    enterprise: str = ""
    custom_addons: list[str] = field(default_factory=list)
    version: str = ""
    branch: str = ""
    enterprise_branch: str = ""
    database: str = ""
    db_filter: str = ""
    http_port: int = 8069
    gevent_port: int = 8072
    data_dir: str = "~/.local/share/Odoo"
    dev: str = ""
    demo: bool = False
    extra_args: list[str] = field(default_factory=list)
    conf: str = ""
    # Set only for worktrees created by `manageodoo worktree add`.
    managed_worktree: bool = False
    wt_community_origin: str = ""   # origin repo, for `git worktree remove`
    wt_enterprise_origin: str = ""

    # ---- serialization -------------------------------------------------
    @classmethod
    def from_dict(cls, name: str, d: dict) -> "Environment":
        kwargs = {"name": name}
        for key in _STR:
            if key in d:
                kwargs[key] = d[key]
        for key in _LIST:
            if key in d:
                kwargs[key] = list(d[key])
        for key in _INT:
            if key in d:
                kwargs[key] = int(d[key])
        for key in _BOOL:
            if key in d:
                kwargs[key] = bool(d[key])
        return cls(**kwargs)

    def to_dict(self) -> dict:
        """Serializable table (drops name, empty strings/lists, and False)."""
        d = asdict(self)
        d.pop("name")
        return {k: v for k, v in d.items()
                if v != "" and v != [] and v is not False}

    # ---- derived values ------------------------------------------------
    def python_exe(self) -> str:
        return self.python or "python3"

    def expanded_data_dir(self) -> str:
        return os.path.expanduser(self.data_dir) if self.data_dir else ""

    def _addons_candidates(self) -> list[str]:
        """All configured addons entries in precedence order, de-duped:
        enterprise (first, wins) -> custom addons -> community/addons.
        The base <community>/odoo/addons is implicit and left off."""
        parts: list[str] = []
        if self.enterprise:
            parts.append(self.enterprise)
        parts.extend(self.custom_addons)
        if self.community:
            parts.append(str(Path(self.community) / "addons"))
        seen: set[str] = set()
        out: list[str] = []
        for p in parts:
            rp = str(Path(p).expanduser())
            if rp not in seen:
                seen.add(rp)
                out.append(rp)
        return out

    def addons_path(self) -> list[str]:
        """Configured addons entries that actually exist (what Odoo receives)."""
        return [p for p in self._addons_candidates() if Path(p).is_dir()]

    def missing_addons(self) -> list[str]:
        """Configured addons entries whose directory is missing (dropped from
        the command line; callers should warn rather than fail silently)."""
        return [p for p in self._addons_candidates() if not Path(p).is_dir()]

    def major_version(self) -> int | None:
        """Numeric major from a series like '19.5' or 'saas~18.2'; None if odd."""
        if not self.version:
            return None
        import re
        m = re.search(r"(\d+)", self.version)
        return int(m.group(1)) if m else None
