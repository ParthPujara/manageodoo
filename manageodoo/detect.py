"""Understand an Odoo install by scanning its layout.

Given a container path, locate the community source root, the odoo-bin to run,
a Python interpreter that carries Odoo's deps, the enterprise addons dir, any
custom addons dirs, the real version (from ``odoo/release.py`` — folder names
lie), and the git branch of each repo.

Everything is best-effort: missing pieces come back empty with a warning rather
than raising, so bare extracts (no git, no venv) still register and degrade
gracefully at run time.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .errors import DetectionError

# Dirs never treated as addons collections when scanning a root.
_SKIP_NAMES = {"venv", ".venv", ".git", "__pycache__", "setup", "debian", "doc"}


@dataclass
class DetectResult:
    root: str
    community: str
    odoo_bin: str
    python: str = ""
    enterprise: str = ""
    custom_addons: list[str] = field(default_factory=list)
    version: str = ""
    branch: str = ""
    enterprise_branch: str = ""
    warnings: list[str] = field(default_factory=list)


def _is_module(p: Path) -> bool:
    return p.is_dir() and (p / "__manifest__.py").is_file() and (p / "__init__.py").is_file()


def _is_addons_collection(p: Path) -> bool:
    """A dir Odoo would accept in ``--addons-path``: holds >=1 module subdir."""
    if not p.is_dir():
        return False
    try:
        return any(_is_module(child) for child in p.iterdir())
    except OSError:
        return False


def _is_community_root(p: Path) -> bool:
    return (p / "odoo-bin").is_file() and (p / "odoo" / "release.py").is_file()


def _find_community(root: Path) -> tuple[Path, list[Path]]:
    """Return (chosen community root, all odoo-bin dirs found up to depth 1)."""
    candidates: list[Path] = []
    if _is_community_root(root):
        candidates.append(root)
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if _is_community_root(child):
            candidates.append(child)
    if not candidates:
        raise DetectionError(
            f"No Odoo source found under {root} "
            "(need a dir with both 'odoo-bin' and 'odoo/release.py')."
        )
    # Prefer the top-level repo, then a 'community' subdir, then first found.
    def rank(p: Path) -> tuple[int, str]:
        if p == root:
            return (0, "")
        if p.name == "community":
            return (1, "")
        return (2, p.name)

    candidates.sort(key=rank)
    return candidates[0], candidates


def _find_python(root: Path, community: Path) -> str:
    """Locate a venv interpreter; empty string if none (caller warns)."""
    seen: list[Path] = []
    for base in (root, community, community.parent):
        for name in ("venv", ".venv"):
            cand = base / name / "bin" / "python"
            if cand.is_file() and cand not in seen:
                seen.append(cand)
    return str(seen[0]) if seen else ""


def _git_branch(repo: Path) -> str:
    """Current branch, or '' if not a real repo (empty .git dirs don't count)."""
    try:
        top = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if top.returncode != 0:
            return ""
        out = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _read_version(community: Path) -> str:
    """Series from odoo/release.py (e.g. '19.5', 'saas~18.2'); '' if unknown."""
    release = community / "odoo" / "release.py"
    if not release.is_file():
        return ""
    snippet = (
        "import runpy, json, sys; "
        "g = runpy.run_path(sys.argv[1]); "
        "print(json.dumps({'series': g.get('series'), 'version': g.get('version')}))"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", snippet, str(release)],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            return str(data.get("series") or data.get("version") or "")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    # Regex fallback: handles string version_info[0] like 'saas~18'.
    text = release.read_text(errors="replace")
    m = re.search(r"series\s*=\s*['\"]([^'\"]+)['\"]", text)
    return m.group(1) if m else ""


def _find_enterprise(root: Path, community: Path) -> Path | None:
    for cand in (root / "enterprise", community.parent / "enterprise"):
        if _is_addons_collection(cand):
            return cand
    return None


def _find_custom_addons(root: Path, community: Path, enterprise: Path | None) -> list[str]:
    """Detect extra addons dirs: collections (e.g. design-themes) plus, if any
    lone module sits directly under root, root itself. Informational only —
    registration is opt-in via --addons (the CLI clears these unless asked)."""
    result: list[str] = []
    exclude = {community, enterprise, root / "addons"}
    add_root = False
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return result
    for child in children:
        if child in exclude or child.name in _SKIP_NAMES:
            continue
        if _is_module(child):
            add_root = True
        elif _is_addons_collection(child):
            result.append(str(child))
    if add_root and str(root) not in result:
        result.append(str(root))
    return result


def detect(path: str) -> DetectResult:
    """Inspect ``path`` and return a resolved layout (raises DetectionError only
    if no Odoo source is found at all)."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise DetectionError(f"Not a directory: {root}")

    community, all_bins = _find_community(root)
    res = DetectResult(
        root=str(root),
        community=str(community),
        odoo_bin=str(community / "odoo-bin"),
    )

    if len(all_bins) > 1:
        others = ", ".join(str(p / "odoo-bin") for p in all_bins if p != community)
        res.warnings.append(
            f"Multiple odoo-bin found; using {res.odoo_bin}. Others: {others}"
        )

    res.python = _find_python(root, community)
    if not res.python:
        res.warnings.append(
            "No venv found; run will fall back to 'python3' (must have Odoo deps)."
        )

    res.version = _read_version(community)
    if not res.version:
        res.warnings.append("Could not read version from odoo/release.py.")

    res.branch = _git_branch(community)
    if not res.branch:
        res.warnings.append(f"{community} is not a git repo (no branch tracked).")

    ent = _find_enterprise(root, community)
    if ent is not None:
        res.enterprise = str(ent)
        res.enterprise_branch = _git_branch(ent)
        if res.branch and res.enterprise_branch and res.branch != res.enterprise_branch:
            res.warnings.append(
                f"Branch drift: community '{res.branch}' vs "
                f"enterprise '{res.enterprise_branch}'."
            )

    res.custom_addons = _find_custom_addons(root, community, ent)
    return res
