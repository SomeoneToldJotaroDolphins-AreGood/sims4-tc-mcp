"""Live socket bridge: in-game companion script + outbound client.

The generated companion module opens a **non-blocking TCP listener**
(loopback by default) on a background thread using the game's embedded
CPython 3.7.  Agents connect from the host and speak newline-delimited
JSON: ``{"token": "...", "command": "ping", "args": {...}}``.

Commands: ``ping``, ``reload``, ``exec``, ``telemetry``, ``shutdown``.

Security defaults: loopback-only bind, shared token required, ``exec``
disabled unless the generator was told to allow it.  This is a local
developer tool, not a network service.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass

DEFAULT_PORT = 45791

_BRIDGE_TEMPLATE = '''# -*- coding: utf-8 -*-
# sims4-tc-mcp live bridge -- Python 3.7 compatible
import json
import socket
import threading
import traceback

HOST = {host!r}
PORT = {port!r}
TOKEN = {token!r}
ALLOW_EXEC = {allow_exec!r}

_server = None
_thread = None
_running = False


def _handle(request):
    token = request.get("token")
    if TOKEN and token != TOKEN:
        return {{"ok": False, "error": "unauthorized"}}
    command = request.get("command", "")
    args = request.get("args") or {{}}
    try:
        if command == "ping":
            return {{"ok": True, "pong": True, "version": "1.0"}}
        if command == "telemetry":
            import sys
            return {{"ok": True, "python": sys.version,
                    "modules": len(sys.modules)}}
        if command == "reload":
            import importlib
            name = args.get("module", "")
            mod = importlib.import_module(name)
            importlib.reload(mod)
            return {{"ok": True, "reloaded": name}}
        if command == "exec":
            if not ALLOW_EXEC:
                return {{"ok": False, "error": "exec disabled"}}
            ns = {{}}
            exec(compile(args.get("code", ""), "<bridge>", "exec"), ns)
            return {{"ok": True,
                    "result": repr(ns.get("result"))}}
        if command == "shutdown":
            return {{"ok": True, "shutdown": True}}
        return {{"ok": False, "error": "unknown command " + command}}
    except Exception:
        return {{"ok": False, "error": traceback.format_exc()}}


def _serve(conn):
    fh = conn.makefile("rwb")
    try:
        for line in fh:
            if not line.strip():
                continue
            try:
                request = json.loads(line.decode("utf-8"))
            except Exception:
                response = {{"ok": False, "error": "bad json"}}
            else:
                response = _handle(request)
            fh.write((json.dumps(response) + "\\n").encode("utf-8"))
            fh.flush()
            if response.get("shutdown"):
                _stop()
                break
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _stop():
    global _running
    _running = False
    if _server is not None:
        try:
            _server.close()
        except Exception:
            pass


def start():
    global _server, _thread, _running
    if _running:
        return
    _server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _server.bind((HOST, PORT))
    _server.listen(4)
    _server.settimeout(1.0)
    _running = True

    def _accept_loop():
        while _running:
            try:
                conn, _addr = _server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(30.0)
            worker = threading.Thread(target=_serve, args=(conn,))
            worker.daemon = True
            worker.start()

    _thread = threading.Thread(target=_accept_loop)
    _thread.daemon = True
    _thread.start()


def stop():
    _stop()


start()
'''


def generate_bridge_script(host: str = "127.0.0.1",
                           port: int = DEFAULT_PORT,
                           token: str = "",
                           allow_exec: bool = False) -> str:
    """Render the in-game bridge module source."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            "refusing to bind a non-loopback host; the bridge is local-only")
    if not (0 < port < 65536):
        raise ValueError("port out of range")
    return _BRIDGE_TEMPLATE.format(host=host, port=port, token=token,
                                   allow_exec=allow_exec)


@dataclass
class BridgeResponse:
    ok: bool
    raw: dict

    def as_dict(self) -> dict:
        return dict(self.raw)


def bridge_command(host: str, port: int, command: str,
                   args: dict | None = None, token: str = "",
                   timeout: float = 10.0,
                   allow_exec: bool = False) -> BridgeResponse:
    """Send one command to a running game and return its response."""
    if command == "exec" and not allow_exec:
        raise ValueError(
            "exec is disabled on the client; pass allow_exec=True "
            "explicitly to acknowledge the risk")
    payload = json.dumps({"token": token, "command": command,
                          "args": args or {}}) + "\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload.encode("utf-8"))
        fh = sock.makefile("rb")
        line = fh.readline()
    if not line:
        raise ConnectionError("bridge closed the connection without a reply")
    raw = json.loads(line.decode("utf-8"))
    return BridgeResponse(ok=bool(raw.get("ok")), raw=raw)
