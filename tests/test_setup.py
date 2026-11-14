"""Auto-setup tests (detection, token, config, service unit)."""

import json
import stat
import sys

import pytest

from sims4_mcp import config, setup


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Isolate HOME, config paths and caches."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(setup, "APP_DIR", tmp_path / ".config" / "sims4-tc-mcp")
    monkeypatch.setattr(setup, "CONFIG_FILE", setup.APP_DIR / "config.json")
    monkeypatch.setattr(setup, "TOKEN_FILE", setup.APP_DIR / "token")
    monkeypatch.setattr(setup, "ENV_FILE", setup.APP_DIR / "env")
    monkeypatch.setattr(setup, "SERVICE_FILE",
                        tmp_path / ".config" / "systemd" / "user"
                        / "sims4-tc-mcp.service")
    monkeypatch.setattr(setup, "DESKTOP_FILE",
                        tmp_path / ".local" / "share" / "applications"
                        / "sims4-tc-mcp.desktop")
    return tmp_path


def _make_steam_prefix(home, user="steamuser"):
    docs = (home / ".steam/steam/steamapps/compatdata/1222670/pfx/drive_c"
            / "users" / user / "Documents/Electronic Arts/The Sims 4")
    (docs / "Mods").mkdir(parents=True)
    (docs / "Saves").mkdir()
    return docs


def test_detects_steam_proton_user_dir(sandbox):
    docs = _make_steam_prefix(sandbox)
    found = setup.detect_user_dir()
    assert found == docs
    paths = setup.detect_paths()
    assert paths["SIMS4_USER_DIR"] == str(docs)
    assert paths["SIMS4_MODS_DIR"] == str(docs / "Mods")


def test_detects_native_documents_dir(sandbox):
    docs = sandbox / "Documents/Electronic Arts/The Sims 4"
    (docs / "Mods").mkdir(parents=True)
    assert setup.detect_user_dir() == docs


def test_prefers_prefix_with_mods(sandbox):
    _make_steam_prefix(sandbox, user="steamuser")
    other = sandbox / "Documents/Electronic Arts/The Sims 4"
    other.mkdir(parents=True)
    found = setup.detect_user_dir()
    assert (found / "Mods").is_dir()


def test_detects_extracted_indexes(sandbox):
    tuning = sandbox / "sims4/extracted/tuning"
    tuning.mkdir(parents=True)
    (tuning / "buff.xml").write_text("<Buff/>")
    tdesc = sandbox / "sims4/extracted/tdesc"
    tdesc.mkdir(parents=True)
    (tdesc / "trait.tdesc").write_text("<tdesc/>")
    scripts = sandbox / "sims4/decompiled"
    (scripts / "base").mkdir(parents=True)
    (scripts / "base" / "sims.py").write_text("x = 1\n")

    paths = setup.detect_paths()
    assert paths["SIMS4_EXTRACTED_TUNING_DIR"] == str(tuning)
    assert paths["SIMS4_TDESC_DIR"] == str(tdesc)
    assert paths["SIMS4_DECOMPILED_SCRIPTS_DIR"] == str(scripts)


def test_token_is_stable_and_private(sandbox):
    first = setup.ensure_token()
    second = setup.ensure_token()
    assert first == second and len(first) == 48
    mode = stat.S_IMODE(setup.TOKEN_FILE.stat().st_mode)
    assert mode == 0o600


def test_port_falls_back_when_busy(sandbox):
    import socket
    with socket.socket() as sock:
        sock.bind(("0.0.0.0", 0))
        sock.listen(1)
        busy = sock.getsockname()[1]
        chosen = setup.pick_port(busy, attempts=10)
    assert chosen != busy


def test_build_and_write_config(sandbox, monkeypatch):
    docs = _make_steam_prefix(sandbox)
    cfg = setup.build_config(host="0.0.0.0", port=7999, token="t" * 48)
    assert cfg["transport"] == "streamable-http"
    assert cfg["paths"]["SIMS4_USER_DIR"] == str(docs)
    assert cfg["token"] == "t" * 48
    path = setup.write_config(cfg)
    assert json.loads(path.read_text())["port"] == 7999
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_file_contains_paths(sandbox):
    _make_steam_prefix(sandbox)
    cfg = setup.build_config(port=7999, token="abc")
    env = setup.write_env_file(cfg).read_text()
    assert "SIMS4_MCP_TRANSPORT=streamable-http" in env
    assert "SIMS4_MCP_AUTH_TOKEN=abc" in env
    assert "SIMS4_USER_DIR=" in env


def test_service_unit_points_at_checkout(sandbox):
    cfg = setup.build_config(port=7999, token="abc")
    unit = setup.service_unit(cfg)
    assert "[Service]" in unit
    assert str(setup.ENV_FILE) in unit
    assert "sims4-tc-mcp" in unit


def test_launcher_entry_is_executable_desktop_file(sandbox):
    cfg = setup.build_config(port=7999, token="abc")
    entry = setup.launcher_entry()
    assert entry.startswith("[Desktop Entry]")
    assert "sims4-tc-mcp-setup" in entry
    path = setup.install_launcher()
    assert stat.S_IMODE(path.stat().st_mode) & 0o111


def test_client_snippet_shape(sandbox):
    cfg = setup.build_config(port=7999, token="abc")
    snippet = setup.client_snippet(cfg, host="10.0.0.5")
    entry = snippet["sims4-tc-mcp"]
    assert entry["url"] == "http://10.0.0.5:7999/mcp"
    assert entry["headers"]["Authorization"] == "Bearer abc"


def test_config_module_reads_saved_config(sandbox, monkeypatch):
    _make_steam_prefix(sandbox)
    cfg = setup.build_config(host="0.0.0.0", port=7777, token="saved-token")
    setup.write_config(cfg)
    monkeypatch.setenv("SIMS4_MCP_CONFIG", str(setup.CONFIG_FILE))
    for key in ("SIMS4_MCP_TRANSPORT", "SIMS4_MCP_HOST", "SIMS4_MCP_PORT",
                "SIMS4_MCP_AUTH_TOKEN", "SIMS4_USER_DIR"):
        monkeypatch.delenv(key, raising=False)

    assert config.transport() == "streamable-http"
    assert config.http_host() == "0.0.0.0"
    assert config.http_port() == 7777
    assert config.auth_token() == "saved-token"
    docs = cfg["paths"]["SIMS4_USER_DIR"]
    assert config.sims4_user_dir() is not None
    assert str(config.sims4_user_dir()) == docs


def test_env_overrides_saved_config(sandbox, monkeypatch):
    cfg = setup.build_config(port=7777, token="saved-token")
    setup.write_config(cfg)
    monkeypatch.setenv("SIMS4_MCP_CONFIG", str(setup.CONFIG_FILE))
    monkeypatch.setenv("SIMS4_MCP_PORT", "8888")
    monkeypatch.setenv("SIMS4_MCP_AUTH_TOKEN", "env-token")
    assert config.http_port() == 8888
    assert config.auth_token() == "env-token"


def test_setup_main_writes_config(sandbox, monkeypatch):
    _make_steam_prefix(sandbox)
    called = {}
    monkeypatch.setattr(setup, "pick_port", lambda *a, **k: 7999)
    import sims4_mcp.server as server
    monkeypatch.setattr(server, "run_http", lambda: called.setdefault("run", 1))
    monkeypatch.setattr(sys, "argv", ["sims4-tc-mcp-setup"])
    assert setup.main([]) == 0
    assert called.get("run") == 1
    saved = json.loads(setup.CONFIG_FILE.read_text())
    assert saved["port"] == 7999
    assert saved["token"]


def test_status_without_config(sandbox, capsys):
    assert setup.status() == 1
    assert "not configured" in capsys.readouterr().out


def test_extractor_command_prefers_path(monkeypatch, tmp_path):
    monkeypatch.setattr(setup.shutil, "which",
                        lambda name: "/usr/bin/ts4-extract"
                        if name == "ts4-extract" else None)
    assert setup.extractor_command() == ["/usr/bin/ts4-extract"]


def test_extractor_command_falls_back_to_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(setup, "EXTRACTOR_DEFAULT_CHECKOUT", tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    command = setup.extractor_command()
    assert command and command[-1] == "ts4-extract"
    assert "run" in command


def test_extractor_command_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    monkeypatch.delenv("TS4_EXTRACT_DIR", raising=False)
    monkeypatch.setattr(setup, "EXTRACTOR_DEFAULT_CHECKOUT",
                        tmp_path / "missing")
    assert setup.extractor_command() is None
