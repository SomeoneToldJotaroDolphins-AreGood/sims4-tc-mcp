"""One-click setup for the Sims 4 MCP server.

Detects the game's user folder (native Linux, EA app, Steam Play/Proton,
Lutris, Flatpak Steam), generates a bearer token, writes a config file so
later starts need no environment variables, and can install a systemd user
service plus a desktop launcher.

Usage::

    uv run sims4-tc-mcp-setup              # detect, write config, serve
    uv run sims4-tc-mcp-setup --service    # ... and install autostart
    uv run sims4-tc-mcp-setup --launcher   # ... and add a menu icon
    uv run sims4-tc-mcp-setup --status     # show what was detected
    uv run sims4-tc-mcp-setup --print-client-config
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

APP_NAME = "sims4-tc-mcp"
APP_DIR = Path.home() / ".config" / APP_NAME
CONFIG_FILE = APP_DIR / "config.json"
TOKEN_FILE = APP_DIR / "token"
ENV_FILE = APP_DIR / "env"
SERVICE_FILE = Path.home() / ".config" / "systemd" / "user" / f"{APP_NAME}.service"
DESKTOP_FILE = (Path.home() / ".local" / "share" / "applications"
                / f"{APP_NAME}.desktop")

SIMS4_STEAM_APPID = "1222670"
DEFAULT_PORT = 7348

_PATH_KEYS = (
    "SIMS4_USER_DIR",
    "SIMS4_MODS_DIR",
    "SIMS4_EXTRACTED_TUNING_DIR",
    "SIMS4_TDESC_DIR",
    "SIMS4_DECOMPILED_SCRIPTS_DIR",
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def user_dir_candidates() -> list[Path]:
    """Every plausible ``Documents/Electronic Arts/The Sims 4`` location."""
    home = Path.home()
    found: list[Path] = [
        home / "Documents" / "Electronic Arts" / "The Sims 4",
        home / ".local/share/feral-interactive/TheSims4/VFS/Prefs"
        / "Electronic Arts" / "The Sims 4",
    ]
    steam_roots = [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    users_glob = "*/Documents/Electronic Arts/The Sims 4"
    for root in steam_roots:
        compat = root / "steamapps" / "compatdata" / SIMS4_STEAM_APPID
        found += sorted((compat / "pfx" / "drive_c" / "users").glob(users_glob))
    found += sorted((home / "Games").glob("*/drive_c/users/" + users_glob))

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def detect_user_dir() -> Path | None:
    """Pick the user folder, preferring one that already has saves/mods."""
    candidates = [p for p in user_dir_candidates() if p.is_dir()]
    for path in candidates:
        if (path / "Mods").is_dir() or (path / "Saves").is_dir():
            return path
    return candidates[0] if candidates else None


def _contains(directory: Path, suffix: str, minimum: int = 1) -> bool:
    try:
        count = 0
        for _ in directory.rglob(f"*{suffix}"):
            count += 1
            if count >= minimum:
                return True
    except OSError:
        return False
    return False


def detect_tuning_dir() -> Path | None:
    home = Path.home()
    for path in (home / "sims4/extracted/tuning",
                 home / "Sims4/extracted/tuning",
                 home / "Documents/Sims 4 Studio/Tuning"):
        if path.is_dir() and _contains(path, ".xml"):
            return path
    return None


def detect_tdesc_dir() -> Path | None:
    home = Path.home()
    for path in (home / "sims4/extracted/tdesc",
                 home / "Sims4/extracted/tdesc"):
        if path.is_dir() and _contains(path, ".tdesc"):
            return path
    return None


def detect_scripts_dir() -> Path | None:
    home = Path.home()
    for path in (home / "sims4/decompiled", home / "Sims4/decompiled"):
        if path.is_dir() and _contains(path, ".py"):
            return path
    return None


def detect_paths() -> dict[str, str | None]:
    user = detect_user_dir()
    return {
        "SIMS4_USER_DIR": str(user) if user else None,
        "SIMS4_MODS_DIR": (str(user / "Mods")
                           if user and (user / "Mods").is_dir() else None),
        "SIMS4_EXTRACTED_TUNING_DIR": _as_str(detect_tuning_dir()),
        "SIMS4_TDESC_DIR": _as_str(detect_tdesc_dir()),
        "SIMS4_DECOMPILED_SCRIPTS_DIR": _as_str(detect_scripts_dir()),
    }


def _as_str(path: Path | None) -> str | None:
    return str(path) if path else None


# ---------------------------------------------------------------------------
# Token, port, network
# ---------------------------------------------------------------------------


def ensure_token() -> str:
    """Reuse the stored token, or create one with 0600 permissions."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            _chmod(TOKEN_FILE, 0o600)
            return token
    token = secrets.token_hex(24)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    _chmod(TOKEN_FILE, 0o600)
    return token


def _chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def stop_service() -> None:
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "stop", f"{APP_NAME}.service"],
                       check=False)


def _port_free(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(preferred: int = DEFAULT_PORT, host: str = "0.0.0.0",
              attempts: int = 50) -> int:
    for port in range(preferred, preferred + attempts):
        if _port_free(port, host):
            return port
    raise RuntimeError(
        f"no free TCP port in {preferred}-{preferred + attempts - 1}")


def lan_ip() -> str | None:
    """Best-effort local address (does not send any packets)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def is_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------


def build_config(host: str = "0.0.0.0", port: int | None = None,
                 token: str | None = None,
                 overrides: dict[str, str | None] | None = None) -> dict:
    paths = detect_paths()
    for key, value in (overrides or {}).items():
        if value:
            paths[key] = value
    return {
        "transport": "streamable-http",
        "host": host,
        "port": port if port is not None else pick_port(DEFAULT_PORT, host),
        "token": token if token is not None else ensure_token(),
        "paths": paths,
    }


def write_config(config: dict) -> Path:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n",
                           encoding="utf-8")
    _chmod(CONFIG_FILE, 0o600)
    return CONFIG_FILE


def write_env_file(config: dict) -> Path:
    """Environment file consumed by the systemd unit."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"SIMS4_MCP_TRANSPORT={config['transport']}",
        f"SIMS4_MCP_HOST={config['host']}",
        f"SIMS4_MCP_PORT={config['port']}",
        f"SIMS4_MCP_AUTH_TOKEN={config['token']}",
    ]
    for key, value in config.get("paths", {}).items():
        if value:
            lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _chmod(ENV_FILE, 0o600)
    return ENV_FILE


def client_snippet(config: dict, host: str | None = None) -> dict:
    """The OpenCode ``mcp.servers`` entry for a client on the LAN."""
    address = host or lan_ip() or "127.0.0.1"
    return {
        "sims4-tc-mcp": {
            "type": "remote",
            "url": f"http://{address}:{config['port']}/mcp",
            "headers": {"Authorization": f"Bearer {config['token']}"},
            "timeout": {"startup": 20000},
        }
    }


def repo_root() -> Path:
    """The checkout this package was installed from (editable install)."""
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd()


def uv_binary() -> str:
    return shutil.which("uv") or "uv"


# ---------------------------------------------------------------------------
# systemd + desktop launcher
# ---------------------------------------------------------------------------


def service_unit(config: dict) -> str:
    repo = repo_root()
    uv = uv_binary()
    return f"""[Unit]
Description=Sims 4 MCP server ({APP_NAME})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={repo}
EnvironmentFile={ENV_FILE}
ExecStart={uv} run --directory {repo} sims4-tc-mcp
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_service(config: dict) -> Path:
    write_env_file(config)
    SERVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_FILE.write_text(service_unit(config), encoding="utf-8")
    if shutil.which("systemctl"):
        for args in (["daemon-reload"],
                     ["enable", "--now", f"{APP_NAME}.service"]):
            subprocess.run(["systemctl", "--user", *args], check=False)
    return SERVICE_FILE


def launcher_entry() -> str:
    repo = repo_root()
    uv = uv_binary()
    command = f'cd "{repo}" && "{uv}" run sims4-tc-mcp-setup --tui'
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Sims 4 MCP Server
Comment=Start the Sims 4 MCP server (auto-detects game folders)
Exec=bash -lc '{command}'
Terminal=true
Icon=utilities-terminal
Categories=Development;Utility;
Keywords=sims4;mcp;modding;
"""


def install_launcher() -> Path:
    DESKTOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_FILE.write_text(launcher_entry(), encoding="utf-8")
    _chmod(DESKTOP_FILE, 0o755)
    return DESKTOP_FILE


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summary(config: dict) -> str:
    paths = config.get("paths", {})
    lines = [
        f"config : {CONFIG_FILE}",
        f"token  : {TOKEN_FILE}",
        f"url    : http://{lan_ip() or '127.0.0.1'}:{config['port']}/mcp",
        "",
        "detected game paths:",
    ]
    for key in _PATH_KEYS:
        lines.append(f"  {key:<30} {paths.get(key) or '— not found'}")
    missing = [k for k in _PATH_KEYS if not paths.get(k)]
    if missing:
        lines += ["", "tip: pass --user-dir/--tuning-dir/--tdesc-dir/--scripts-dir",
                  "     to override anything that was not detected."]
    lines += ["", "client config (merge under mcp.servers):",
              json.dumps(client_snippet(config), indent=2)]
    return "\n".join(lines)


def status() -> int:
    if not CONFIG_FILE.exists():
        print(f"not configured yet ({CONFIG_FILE} missing)")
        print("run: uv run sims4-tc-mcp-setup")
        return 1
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    print(summary(config))
    state = ("running" if is_listening(int(config["port"]))
             else "not running")
    print(f"\nserver : {state} on port {config['port']}")
    return 0


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------


def _apply_overrides(config: dict,
                     overrides: dict[str, str | None] | None) -> dict:
    paths = config.setdefault("paths", {})
    for key, value in (overrides or {}).items():
        if value:
            paths[key] = value
    return config


def _load_or_build(host: str = "0.0.0.0",
                   overrides: dict[str, str | None] | None = None) -> dict:
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(config, dict) and config.get("token"):
                return _apply_overrides(config, overrides)
        except ValueError:
            pass
    config = build_config(host=host, overrides=overrides)
    write_config(config)
    write_env_file(config)
    return config


def _dashboard(console, config: dict) -> None:
    from rich.table import Table

    paths = config.get("paths", {})
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="ellipsis", max_width=56)
    url = f"http://{lan_ip() or '127.0.0.1'}:{config['port']}/mcp"
    running = is_listening(int(config["port"]))
    table.add_row("server", f"[green]running[/green]  {url}"
                  if running else f"[yellow]stopped[/yellow]  {url}")
    for key in _PATH_KEYS:
        value = paths.get(key)
        table.add_row(key.replace("SIMS4_", "").lower(),
                      value or "[dim]not found[/dim]")
    table.add_row("token", str(TOKEN_FILE))
    console.print(table)


def tui(host: str = "0.0.0.0",
        overrides: dict[str, str | None] | None = None) -> int:
    """Interactive menu for non-technical users."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt

    console = Console()
    config = _load_or_build(host, overrides)
    choices = ["1", "2", "3", "4", "5", "6", "7", "q"]

    while True:
        if console.is_terminal:
            console.clear()
        console.print(Panel.fit(
            "[bold]Sims 4 MCP Server[/bold]\n"
            "Build, inspect and debug Sims 4 mods over MCP.",
            border_style="cyan"))
        _dashboard(console, config)
        console.print(
            "\n[bold]1[/bold] Start server now      "
            "[bold]2[/bold] Install background service\n"
            "[bold]3[/bold] Re-detect game folders "
            "[bold]4[/bold] Change port\n"
            "[bold]5[/bold] Show client config    "
            "[bold]6[/bold] Add desktop icon\n"
            "[bold]7[/bold] Stop background service "
            "[bold]q[/bold] Quit")

        choice = Prompt.ask("Choose", choices=choices, default="1")

        if choice == "q":
            return 0
        if choice == "1":
            write_config(config)
            write_env_file(config)
            console.print("[dim]Press Ctrl+C to stop.[/dim]")
            try:
                from . import server
                server.run_http()
            except KeyboardInterrupt:
                pass
        elif choice == "2":
            write_config(config)
            path = install_service(config)
            console.print(f"[green]service installed:[/green] {path}")
            console.print(f"  systemctl --user status {APP_NAME}")
            console.print(f"[yellow]tip:[/yellow] allow TCP {config['port']} "
                          "in your firewall")
        elif choice == "3":
            overrides = detect_paths()
            config["paths"] = overrides
            write_config(config)
            write_env_file(config)
            console.print("[green]re-detected.[/green]")
        elif choice == "4":
            raw = Prompt.ask("Port", default=str(config["port"]))
            try:
                port = int(raw)
                config["port"] = port
                write_config(config)
                write_env_file(config)
            except ValueError:
                console.print("[red]not a number[/red]")
        elif choice == "5":
            console.print_json(json.dumps(client_snippet(config)))
        elif choice == "6":
            console.print(f"[green]launcher:[/green] {install_launcher()}")
        elif choice == "7":
            stop_service()
            console.print("[green]service stopped.[/green]")
        Prompt.ask("Press Enter", default="", show_default=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _service_active() -> bool:
    if not shutil.which("systemctl"):
        return False
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", f"{APP_NAME}.service"],
        check=False)
    return result.returncode == 0


def auto(host: str = "0.0.0.0",
         overrides: dict[str, str | None] | None = None) -> int:
    """Zero-question path: detect, configure, run in the background."""
    config = _load_or_build(host, overrides)
    write_config(config)
    write_env_file(config)
    print(summary(config), flush=True)

    if shutil.which("systemctl"):
        install_service(config)
        if _service_active():
            print(f"\nOK: {APP_NAME} is running in the background", flush=True)
            print(f"     systemctl --user status {APP_NAME}", flush=True)
            return 0
        print("\nsystemd user service could not start; running in the "
              "foreground instead", flush=True)
    from . import server
    server.run_http()
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{APP_NAME}-setup",
        description="Detect Sims 4 folders, configure and start the MCP "
                    "server.")
    parser.add_argument("--tui", action="store_true",
                        help="interactive menu (recommended for desktop use)")
    parser.add_argument("--auto", action="store_true",
                        help="no questions: configure and start in the "
                             "background (systemd user service)")
    parser.add_argument("--serve", action="store_true",
                        help="start the HTTP server after writing config "
                             "(default)")
    parser.add_argument("--service", action="store_true",
                        help="install + enable a systemd user service")
    parser.add_argument("--launcher", action="store_true",
                        help="install a desktop launcher for one-click start")
    parser.add_argument("--status", action="store_true",
                        help="show detection + running state, then exit")
    parser.add_argument("--print-client-config", action="store_true",
                        help="print the OpenCode entry and exit")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--token", default=None,
                        help="use this bearer token instead of generating one")
    parser.add_argument("--user-dir", default=None)
    parser.add_argument("--mods-dir", default=None)
    parser.add_argument("--tuning-dir", default=None)
    parser.add_argument("--tdesc-dir", default=None)
    parser.add_argument("--scripts-dir", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    overrides = {
        "SIMS4_USER_DIR": args.user_dir,
        "SIMS4_MODS_DIR": args.mods_dir,
        "SIMS4_EXTRACTED_TUNING_DIR": args.tuning_dir,
        "SIMS4_TDESC_DIR": args.tdesc_dir,
        "SIMS4_DECOMPILED_SCRIPTS_DIR": args.scripts_dir,
    }
    if args.status:
        return status()
    if args.tui:
        return tui(host=args.host, overrides=overrides)
    if args.auto:
        return auto(host=args.host, overrides=overrides)

    config = build_config(host=args.host, port=args.port, token=args.token,
                          overrides=overrides)
    write_config(config)
    write_env_file(config)

    if args.print_client_config:
        print(json.dumps(client_snippet(config), indent=2), flush=True)
        return 0

    print(summary(config), flush=True)

    if args.service:
        print(f"\nservice: {install_service(config)}", flush=True)
    if args.launcher:
        print(f"launcher: {install_launcher()}", flush=True)

    if args.service:
        print("\ninstalled; manage with:", flush=True)
        print(f"  systemctl --user status {APP_NAME}", flush=True)
        print(f"  journalctl --user -u {APP_NAME} -f", flush=True)
        return 0

    from . import server

    print("\nstarting server (Ctrl+C to stop)…", flush=True)
    server.run_http()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
