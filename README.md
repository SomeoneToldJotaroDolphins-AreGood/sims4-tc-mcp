# sims4-tc-mcp

Disclaimer: This is a vibecoded project 

A **Model Context Protocol** server for building, inspecting, packaging and
debugging **script mods (`.ts4script`)** and **total conversions** for
*The Sims 4*.

Point any MCP-capable agent (OpenCode, Claude Desktop, Cursor, …) at it and
it can hash resources, read/write `.package` files, search vanilla tuning,
validate Python 3.7 for the game's interpreter, patch catalog flags, package
`.ts4script` zips, and parse `lastException` tracebacks.

Built on the official [`mcp`](https://pypi.org/project/mcp/) Python SDK,
`lxml`, `pydantic` v2 and SQLite FTS5.

## Features

| Subsystem | What it does |
|---|---|
| `crypto/fnv.py` | FNV-1 24/32/64 hashing, lowercase normalization, high-bit toggling |
| `dbpf/` | DBPF 2.1 `.package` reader/writer, index v3, constant-encoding flags, ZLIB fallback |
| `stbl/` | STBL v5 codec + one-shot 18-locale dictionaries |
| `tdesc/` | `.tdesc` schema parser, SQLite cache, XML skeletons |
| `simdata/` | `0x545AC67A` v0x101 codec + curated XML→SimData compilers (Trait, Buff, Aspiration) |
| `tuning/` | FTS5 vanilla tuning index + reverse-reference dependency graph |
| `pyengine/` | Decompiled game Python index, Python **3.7.0** AST gatekeeper, pyc-3394 headers, injectors, `.ts4script` packager |
| `catalog/` | CASP `0x034AEECB` / COBJ `0x319E4F1D` / OBJD `0xC0DB5AE7` flag patchers + batch overrides |
| `live_bridge/` | In-game TCP bridge generator, outbound client, `lastException` diagnostics |

## Requirements

- Python **3.11+** (the package pins 3.12 through `uv`)
- [`uv`](https://docs.astral.sh/uv/)
- Linux or macOS; the stdio server also runs on Windows, the auto-setup
  helpers are POSIX-oriented (systemd/desktop launcher)

## Install

```bash
git clone https://github.com/SomeoneToldJotaroDolphins-AreGood/sims4-tc-mcp.git
cd sims4-tc-mcp
uv sync
```

Run the test suite (107 tests):

```bash
uv run pytest
```

## One-click setup

`sims4-tc-mcp-setup` finds your game folder, creates a bearer token, writes
a config file, and starts the server — no environment variables needed:

```bash
uv run sims4-tc-mcp-setup --tui      # interactive menu (recommended)
uv run sims4-tc-mcp-setup --auto     # zero questions, background service
uv run sims4-tc-mcp-setup --status   # what was detected / is it running?
uv run sims4-tc-mcp-setup --launcher # add a desktop icon that opens the TUI
```

The TUI shows detected paths, lets you start the server in the foreground or
as a systemd user service, change the port, and print a ready-to-paste client
config.

Auto-detected game folders:

| Installation | User folder |
|---|---|
| Steam Play / Proton | `~/.steam/steam/steamapps/compatdata/1222670/pfx/drive_c/users/*/Documents/Electronic Arts/The Sims 4` |
| Steam (Flatpak) | `~/.var/app/com.valvesoftware.Steam/.local/share/Steam/.../1222670/...` |
| EA app / native | `~/.local/share/feral-interactive/TheSims4/VFS/Prefs/Electronic Arts/The Sims 4` |
| Wine / Lutris | `~/Games/*/drive_c/users/*/Documents/Electronic Arts/The Sims 4` |
| Native Windows-style | `~/Documents/Electronic Arts/The Sims 4` |

It also picks up `~/sims4/extracted/tuning`, `~/sims4/extracted/tdesc` and
`~/sims4/decompiled` when they exist. Anything missing can be overridden
with `--user-dir/--mods-dir/--tuning-dir/--tdesc-dir/--scripts-dir`.

Settings live in `~/.config/sims4-tc-mcp/config.json` (mode 0600) with the
token in `~/.config/sims4-tc-mcp/token`. Environment variables still take
precedence, so containers and CI keep working.

## Manual run

**stdio** (the default; what desktop MCP clients spawn):

```bash
uv run sims4-tc-mcp
```

Nothing is printed — stdio is a silent JSON-RPC pipe. To poke at it:

```bash
uv run mcp dev src/sims4_mcp/server.py
```

**HTTP** (for remote clients):

```bash
SIMS4_MCP_TRANSPORT=streamable-http \
SIMS4_MCP_HOST=0.0.0.0 \
SIMS4_MCP_PORT=7348 \
SIMS4_MCP_AUTH_TOKEN="$(openssl rand -hex 24)" \
uv run sims4-tc-mcp
```

Binding any non-loopback interface **without** `SIMS4_MCP_AUTH_TOKEN` refuses
to start. Loopback binds may run token-less.

## Configuration

All paths are optional. Without game files the server still works; tools that
need an index report `unconfigured` with a hint instead of failing.

| Variable | Purpose |
|---|---|
| `SIMS4_USER_DIR` | `…/Documents/Electronic Arts/The Sims 4` (default guess used if unset) |
| `SIMS4_MODS_DIR` | Mods folder — default `source_dir` for catalog overrides |
| `SIMS4_EXTRACTED_TUNING_DIR` | Extracted Maxis XML tuning (Sims 4 Studio / Lot51) for FTS + graph |
| `SIMS4_TDESC_DIR` | Extracted `.tdesc` schema files |
| `SIMS4_DECOMPILED_SCRIPTS_DIR` | Decompiled game Python (`base`, `core`, `simulation`, `protocolbuffers`) |
| `SIMS4_MCP_CACHE_DIR` | SQLite cache root (default `~/.cache/sims4-tc-mcp`) |
| `SIMS4_MCP_TRANSPORT` | `stdio` (default), `streamable-http` or `sse` |
| `SIMS4_MCP_HOST` / `SIMS4_MCP_PORT` | HTTP bind (default `127.0.0.1:7348`) |
| `SIMS4_MCP_AUTH_TOKEN` | Bearer token; **required** for any non-loopback bind |
| `SIMS4_MCP_CONFIG` | Config file override (default `~/.config/sims4-tc-mcp/config.json`) |

Build the indexes once the data is available:

```jsonc
// tools: rebuild_indexes(tuning=true, python_api=true, tdesc=true, force=false)
```

## Remote use (game PC → laptop)

Run the server where the game and its files live, connect from anywhere on
the LAN:

```bash
# on the game machine
uv run sims4-tc-mcp-setup --tui     # option 2 installs + starts the service
```

Then open the port on that machine (Fedora example):

```bash
sudo firewall-cmd --add-port=7348/tcp --permanent && sudo firewall-cmd --reload
```

The token is plain HTTP on the LAN; use an SSH tunnel if you cross untrusted
networks:

```bash
ssh -L 7348:127.0.0.1:7348 user@game-pc
```

## OpenCode configuration

`opencode.jsonc` (project root or `~/.config/opencode/opencode.jsonc`).
Servers go under `mcp.servers`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      // local stdio variant (server on the same machine)
      "sims4-tc-mcp": {
        "type": "local",
        "command": ["uv", "run", "--directory", "/path/to/sims4-tc-mcp", "sims4-tc-mcp"],
        "environment": {
          "SIMS4_USER_DIR": "~/Documents/Electronic Arts/The Sims 4",
          "SIMS4_MODS_DIR": "~/Documents/Electronic Arts/The Sims 4/Mods"
        }
      },
      // remote variant (server on the game PC)
      "sims4-tc-mcp-remote": {
        "type": "remote",
        "url": "http://192.168.1.50:7348/mcp",
        "headers": { "Authorization": "Bearer <token>" },
        "timeout": { "startup": 15000 }
      }
    }
  }
}
```

`uv run sims4-tc-mcp-setup --print-client-config` prints exactly this block
with your detected address and token filled in.

## Tools

| Tool | Description |
|---|---|
| `hash_fnv(text, bits, high_bit)` | Hex / decimal / TGI representations |
| `inspect_package(package_path)` | TGI list with types, sizes, names, decoded metadata |
| `extract_package_resource(package_path, tgi, output_path?)` | Decodes XML, STBL, SimData, catalog resources |
| `build_package(output, project_dir)` | Compiles tuning XML + STBL dicts + binaries (+ auto-SimData) |
| `create_stbl_dictionary(strings, base_name, generate_all_locales)` | Writes all 18 locale STBLs, returns `0x` keys |
| `query_tdesc_schema(class_name)` | Schema fields, export modes, starter XML |
| `search_vanilla_tuning(query, class_filter, limit)` | FTS5 search |
| `read_vanilla_tuning(instance_id)` | Full XML |
| `get_tuning_dependency_graph(instance_id, direction, depth)` | Reverse-reference BFS |
| `compile_tuning_with_simdata(xml_content, output_dir)` | Validates XML and emits companion SimData |
| `search_game_python_api(query)` | Classes, methods, signatures, docstrings |
| `generate_runtime_tuning_hook(hook_type, config_json)` | Gatekeeper-checked injection/strip code |
| `validate_and_build_ts4script(source_dir, output_path)` | 3.7 AST checks + deterministic zip |
| `batch_create_catalog_overrides(resource_type, filter_query, …)` | CASP/COBJ/OBJD override package |
| `analyze_last_exception(sims4_docs_dir?)` | Structured tracebacks cross-referenced with indexes |
| `bridge_send_command(host, port, command, …)` | Live hot-reload / telemetry client |
| `rebuild_indexes(…)` | Maintenance for all three SQLite indexes |

Resources: `sims4://config`, `sims4://indexes`.
Prompt: `total_conversion_workflow`.

## Project layout

```
project/
  tuning/*.xml          # tuning documents (i:id or s=/n=)
  *.stbl.json           # {"base_name": "...", "locales": {...}}
  bin/*.bin + *.tgi     # raw binary resources with TGI sidecars
```

```python
build_package("/path/MyMod.package", "/path/project")
```

## Notes & limitations

- **SimData format** is community RE'd and table-specific; the codec is
  generic and round-trip safe, while XML auto-compilation supports the
  registry in `simdata/compilers.py` (extend with `register()`).
- **`.pyc` files**: only 16-byte CPython 3.7 headers (magic 3394) can be
  produced on a newer host. `.ts4script` ships `.py` source, which the game
  compiles itself — that is the supported path.
- **Catalog flags**: CASP `AllowForRandom` uses the officially documented
  partFlags bit 2. COBJ flag-word semantics are partially undocumented; the
  patcher exposes configurable word/bit targets (CASP conventions by default)
  and preserves every other byte.
- **Live bridge** binds loopback only and requires a shared token; `exec` is
  disabled unless explicitly allowed on both sides.

## Development

```bash
uv sync
uv run pytest -q              # 107 tests
uv run pytest -v tests/test_dbpf.py
```

Coverage: FNV known answers, DBPF round-trips across flag combinations,
STBL v5 + 18 locales, SimData codec + compilers, tuning FTS + graph,
gatekeeper matrices, pyc headers, injector templates, packager,
CASP/COBJ/OBJD patching, HTTP transport + bearer auth, setup detection, and
every MCP tool (in-process client included).

## Security

The HTTP transport exposes file-reading and file-writing tools. It requires a
bearer token for every request and refuses to bind a non-loopback interface
without one. Never commit the token (`.gitignore` covers the common names).

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Not affiliated with or endorsed by Electronic Arts, Maxis, or Mod The Sims.
*The Sims* is a trademark of Electronic Arts Inc. This project ships no game
assets; you need your own legally installed copy of the game.
