"""Environment-driven configuration with graceful degradation.

The game files may not be present on the host yet; every path resolver
returns ``None`` when unconfigured so tools can report "not indexed"
instead of crashing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ENV_USER_DIR = "SIMS4_USER_DIR"
_ENV_MODS_DIR = "SIMS4_MODS_DIR"
_ENV_TUNING_DIR = "SIMS4_EXTRACTED_TUNING_DIR"
_ENV_TDESC_DIR = "SIMS4_TDESC_DIR"
_ENV_SCRIPTS_DIR = "SIMS4_DECOMPILED_SCRIPTS_DIR"
_ENV_TRANSPORT = "SIMS4_MCP_TRANSPORT"
_ENV_HTTP_HOST = "SIMS4_MCP_HOST"
_ENV_HTTP_PORT = "SIMS4_MCP_PORT"
_ENV_AUTH_TOKEN = "SIMS4_MCP_AUTH_TOKEN"
_ENV_ALLOW_UNAUTH = "SIMS4_MCP_ALLOW_UNAUTHENTICATED"
_ENV_CONFIG = "SIMS4_MCP_CONFIG"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_DEFAULT_CONFIG = Path.home() / ".config" / "sims4-tc-mcp" / "config.json"


def saved_config_path() -> Path:
    """Where ``sims4-tc-mcp-setup`` stores the auto-detected settings."""
    raw = os.environ.get(_ENV_CONFIG, "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_CONFIG


def _saved_config() -> dict:
    """Config-file fallback (environment variables always win)."""
    try:
        data = json.loads(saved_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        saved = _saved_config().get("paths", {})
        raw = (saved.get(name) or "").strip() if isinstance(saved, dict) else ""
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None



def sims4_user_dir() -> Path | None:
    """``Documents/Electronic Arts/The Sims 4`` (env override first)."""
    p = _resolve_env(_ENV_USER_DIR)
    if p:
        return p
    guess = Path.home() / "Documents" / "Electronic Arts" / "The Sims 4"
    return guess if guess.is_dir() else None


def mods_dir() -> Path | None:
    p = _resolve_env(_ENV_MODS_DIR)
    if p:
        return p
    user = sims4_user_dir()
    if user:
        guess = user / "Mods"
        return guess if guess.is_dir() else None
    return None


def extracted_tuning_dir() -> Path | None:
    return _resolve_env(_ENV_TUNING_DIR)


def tdesc_dir() -> Path | None:
    return _resolve_env(_ENV_TDESC_DIR)


def decompiled_scripts_dir() -> Path | None:
    return _resolve_env(_ENV_SCRIPTS_DIR)


def cache_dir() -> Path:
    """Local SQLite index cache directory (created on demand)."""
    base = os.environ.get("SIMS4_MCP_CACHE_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".cache" / "sims4-tc-mcp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def status() -> dict[str, str | None]:
    """Snapshot of resolved paths for the ``sims4://config`` resource."""
    return {
        "python": sys.version.split()[0],
        _ENV_USER_DIR: str(p) if (p := sims4_user_dir()) else None,
        _ENV_MODS_DIR: str(p) if (p := mods_dir()) else None,
        _ENV_TUNING_DIR: str(p) if (p := extracted_tuning_dir()) else None,
        _ENV_TDESC_DIR: str(p) if (p := tdesc_dir()) else None,
        _ENV_SCRIPTS_DIR: str(p) if (p := decompiled_scripts_dir()) else None,
        "cache_dir": str(cache_dir()),
        "config_file": str(saved_config_path())
        if saved_config_path().exists() else None,
    }


# ---------------------------------------------------------------------------
# Network (streamable-http) transport
# ---------------------------------------------------------------------------


def transport() -> str:
    """``stdio`` (default) or ``streamable-http``."""
    mode = os.environ.get(_ENV_TRANSPORT, "").strip().lower()
    if not mode:
        mode = str(_saved_config().get("transport", "")).strip().lower()
    if not mode:
        mode = "stdio"
    return mode if mode in ("stdio", "streamable-http", "sse") else "stdio"


def http_host() -> str:
    raw = os.environ.get(_ENV_HTTP_HOST, "").strip()
    if not raw:
        raw = str(_saved_config().get("host", "")).strip()
    return raw or "127.0.0.1"


def http_port() -> int:
    raw = os.environ.get(_ENV_HTTP_PORT, "").strip()
    if not raw:
        raw = str(_saved_config().get("port", "")).strip()
    try:
        return int(raw or 7348)
    except ValueError:
        return 7348


def auth_token() -> str:
    token = os.environ.get(_ENV_AUTH_TOKEN, "").strip()
    return token or str(_saved_config().get("token", "")).strip()


def requires_auth(host: str | None = None) -> bool:
    """Whether the HTTP app must enforce the bearer token.

    A token is always enforced when set.  Binding to anything beyond
    loopback (LAN/0.0.0.0) *demands* ``SIMS4_MCP_AUTH_TOKEN`` so the server
    refuses to start unauthenticated on a reachable interface; loopback
    binds may run token-less for local tools and tests.
    """
    host = host if host is not None else http_host()
    token = auth_token()
    if host not in _LOOPBACK_HOSTS and not token:
        raise RuntimeError(
            f"Refusing to bind {host!r} without {_ENV_AUTH_TOKEN}: the MCP "
            "server exposes file-writing tools. Set a bearer token, or bind "
            "127.0.0.1 and use an SSH tunnel.")
    if not token:
        if os.environ.get(_ENV_ALLOW_UNAUTH, "").strip().lower() in (
                "1", "true", "yes"):
            return False
        # loopback + no token: run unauthenticated (local-only by bind)
        return False
    return True

