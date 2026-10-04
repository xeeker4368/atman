"""A pytest plugin for ``tests/test_live_marker.py``: log every network touch of a pytest run.

Load it with ``-p tests.netwatch`` and set ``ANAM_NETWATCH_LOG`` to a file. Every socket
``connect`` and every DNS lookup made while the run is imported, collected or executed is
appended to that file, one line each. It is a watcher, not a guard: it never refuses anything.
"""

from __future__ import annotations

import os
import socket

_LOG = os.environ.get("ANAM_NETWATCH_LOG")


def _note(kind: str, target: object) -> None:
    if _LOG:
        with open(_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{kind} {target}\n")


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def _watched_connect(self, address):
    _note("connect", address)
    return _connect(self, address)


def _watched_connect_ex(self, address):
    _note("connect_ex", address)
    return _connect_ex(self, address)


def _watched_getaddrinfo(host, *args, **kwargs):
    _note("getaddrinfo", host)
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _watched_connect
socket.socket.connect_ex = _watched_connect_ex
socket.getaddrinfo = _watched_getaddrinfo
