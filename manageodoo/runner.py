"""Build and launch the odoo-bin command line for an environment.

We shell out to ``<python> <odoo-bin> -c <generated-conf> [overrides] [raw]``
rather than importing Odoo, so each env stays pinned to its own interpreter.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Iterable, Optional

from .env import Environment
from .errors import RunError
from .odooconf import write_conf


def normalize_test_tags(values: Iterable[str]) -> str:
    """Turn --test values into an Odoo --test-tags spec string.

    A bare ``test_...`` name is a method selector, so it gets the leading dot
    Odoo expects (``.test_foo``). Anything else — full specs like
    ``/module:Class.test_foo``, tag names, ``-`` exclusions — passes through
    untouched. Values may be comma-separated; the result joins with commas."""
    out: list[str] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            if re.fullmatch(r"test_\w+", part):
                part = "." + part
            out.append(part)
    return ",".join(out)


def build_argv(
    env: Environment,
    conf_path: str,
    *,
    database: Optional[str] = None,
    http_port: Optional[int] = None,
    init: Optional[str] = None,
    update: Optional[str] = None,
    dev: Optional[str] = None,
    demo: Optional[bool] = None,
    test: Iterable[str] = (),
    stop_after_init: bool = False,
    log_level: Optional[str] = None,
    raw: Iterable[str] = (),
) -> list[str]:
    """Assemble argv. ``None`` for dev/demo/database means 'use env default'."""
    argv = [env.python_exe(), env.odoo_bin, "-c", conf_path]

    db = database if database is not None else env.database
    if db:
        argv += ["-d", db]
    if http_port is not None:
        argv += ["-p", str(http_port)]
    if init:
        argv += ["-i", init]
    if update:
        argv += ["-u", update]

    eff_dev = dev if dev is not None else env.dev
    if eff_dev:
        argv += ["--dev", eff_dev]

    eff_demo = demo if demo is not None else env.demo
    argv += ["--with-demo"] if eff_demo else ["--without-demo"]

    if test:
        tags = normalize_test_tags(test)
        if tags:
            argv += ["--test-enable", "--test-tags", tags]

    if stop_after_init:
        argv += ["--stop-after-init"]
    if log_level:
        argv += ["--log-level", log_level]

    argv += list(env.extra_args)
    argv += list(raw)
    return argv


def run_env(env: Environment, **overrides) -> int:
    """Regenerate the conf, build the command, exec it, return exit code."""
    if not os.path.isfile(env.odoo_bin):
        raise RunError(f"odoo-bin not found: {env.odoo_bin}")
    py = env.python_exe()
    if os.path.sep in py and not os.path.isfile(py):
        raise RunError(f"python interpreter not found: {py}")

    conf_path = write_conf(env)
    argv = build_argv(env, conf_path, **overrides)
    # Launch from the community source dir rather than inheriting the shell's
    # cwd. If the user started us from a deleted/recreated dir (common after
    # removing and re-adding a worktree), the inherited cwd is a stale inode and
    # os.getcwd() inside Odoo (e.g. libsass) raises FileNotFoundError, 500-ing
    # every asset. A known-valid cwd avoids that.
    cwd = env.community if env.community and os.path.isdir(env.community) else None
    return subprocess.call(argv, cwd=cwd)
