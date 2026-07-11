"""Minimal port allocation for the MVP.

Assign each new environment an ``(http_port, gevent_port)`` pair that does not
collide with ports already recorded in the registry. Live-socket probing and a
richer allocator arrive with the worktree phase; here we only avoid overlap with
other registered envs.
"""

from __future__ import annotations

from typing import Any


def gevent_for(http_port: int, defaults: dict[str, Any]) -> int:
    return http_port + int(defaults.get("gevent_offset", 3))


def used_ports(doc: dict[str, Any]) -> set[int]:
    used: set[int] = set()
    for entry in doc.get("env", {}).values():
        for key in ("http_port", "gevent_port"):
            if key in entry:
                used.add(int(entry[key]))
    return used


def allocate(doc: dict[str, Any], defaults: dict[str, Any]) -> tuple[int, int]:
    """First free (http, gevent) pair walking base by stride, skipping any pair
    that overlaps a port already registered."""
    base = int(defaults.get("http_port_base", 8069))
    stride = int(defaults.get("port_stride", 10))
    used = used_ports(doc)
    http = base
    while True:
        gevent = gevent_for(http, defaults)
        if http not in used and gevent not in used:
            return http, gevent
        http += stride
