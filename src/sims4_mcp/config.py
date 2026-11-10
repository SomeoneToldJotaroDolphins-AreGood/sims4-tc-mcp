"""Environment-driven configuration with graceful degradation.

The game files may not be present on the host yet; every path resolver
returns ``None`` when unconfigured so tools can report "not indexed"
instead of crashing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_USER_DIR = "SIMS4_USER_DIR"
_ENV_MODS_DIR = "SIMS4_MODS_DIR"
_ENV_TUNING_DIR = "SIMS4_EXTRACTED_TUNING_DIR"
_ENV_TDESC_DIR = "SIMS4_TDESC_DIR"
_ENV_SCRIPTS_DIR = "SIMS4_DECOMPILED_SCRIPTS_DIR"


def _resolve_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
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
    }
