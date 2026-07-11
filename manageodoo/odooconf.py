"""Generate a per-environment Odoo ``-c`` INI file.

We never touch the user's global ``~/.odoorc`` (it holds admin_passwd). Instead
each environment gets its own ``[options]`` file that ``run`` passes with ``-c``;
CLI flags still override it (Odoo precedence: CLI > conf > defaults).
"""

from __future__ import annotations

import configparser
from pathlib import Path

from . import config
from .env import Environment


def _gevent_key(env: Environment) -> str:
    """Odoo renamed longpolling_port -> gevent_port in v16."""
    major = env.major_version()
    if major is not None and major < 16:
        return "longpolling_port"
    return "gevent_port"


def write_conf(env: Environment) -> str:
    """Write the env's INI file and return its path."""
    options: dict[str, str] = {}

    addons = env.addons_path()
    if addons:
        options["addons_path"] = ",".join(addons)

    data_dir = env.expanded_data_dir()
    if data_dir:
        options["data_dir"] = data_dir

    options["http_port"] = str(env.http_port)
    options[_gevent_key(env)] = str(env.gevent_port)

    if env.db_filter:
        # Odoo's config-file key is 'dbfilter' (no underscore); 'db_filter'
        # would be stored unparsed and ignored.
        options["dbfilter"] = env.db_filter

    parser = configparser.RawConfigParser()
    parser.add_section("options")
    for key, value in options.items():
        parser.set("options", key, value)

    path = Path(env.conf).expanduser() if env.conf else config.conf_path(env.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        parser.write(fh)
    return str(path)
