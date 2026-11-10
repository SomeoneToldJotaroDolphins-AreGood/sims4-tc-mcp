# sims4-tc-mcp

Production-grade **Model Context Protocol** server for building, inspecting,
packaging and debugging **heavy script mods (`.ts4script`)** and
**large-scale Total Conversions** for *The Sims 4*.

Built on the official `mcp` Python SDK (mcp ≥ 2.x; `FastMCP` is exposed as
`MCPServer` — same `@mcp.tool()` / `@mcp.resource()` / `@mcp.prompt()` API),
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

## Quick start

```bash
# uv is the only prerequisite
pip install --user uv          # or: curl -LsSf https://astral.sh/uv/install.sh | sh

cd Sims4Modding
uv sync                        # creates .venv with Python 3.12
uv run pytest                  # 86 tests
uv run sims4-tc-mcp            # stdio MCP server
```

## Configuration

All paths are optional. Without game files the server still works; tools
that need an index report `unconfigured` with a hint instead of failing.

| Variable | Purpose |
|---|---|
| `SIMS4_USER_DIR` | `…/Documents/Electronic Arts/The Sims 4` (default guess used if unset) |
| `SIMS4_MODS_DIR` | Mods folder — default `source_dir` for catalog overrides |
| `SIMS4_EXTRACTED_TUNING_DIR` | Extracted Maxis XML tuning (Sims 4 Studio / Lot51) for FTS + graph |
| `SIMS4_TDESC_DIR` | Extracted `.tdesc` schema files |
| `SIMS4_DECOMPILED_SCRIPTS_DIR` | Decompiled game Python (`base`, `core`, `simulation`, `protocolbuffers`) |
| `SIMS4_MCP_CACHE_DIR` | Override the SQLite cache root (default `~/.cache/sims4-tc-mcp`) |

Build the indexes once the data is available:

```jsonc
// tools: rebuild_indexes(tuning=true, python_api=true, tdesc=true, force=false)
```

## OpenCode configuration

`opencode.jsonc` (project root or `~/.config/opencode/opencode.jsonc`):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "sims4-tc-mcp": {
      "type": "local",
      "command": [
        "uv", "run", "--directory", "/home/baris/Sims4Modding", "sims4-tc-mcp"
      ],
      "environment": {
        "SIMS4_EXTRACTED_TUNING_DIR": "~/sims4/extracted/tuning",
        "SIMS4_TDESC_DIR": "~/sims4/extracted/tdesc",
        "SIMS4_DECOMPILED_SCRIPTS_DIR": "~/sims4/decompiled",
        "SIMS4_USER_DIR": "~/Documents/Electronic Arts/The Sims 4",
        "SIMS4_MODS_DIR": "~/Documents/Electronic Arts/The Sims 4/Mods"
      },
      "enabled": true
    }
  }
}
```

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
  produced on a newer host. `.ts4script` ships `.py` source, which the
  game compiles itself — that is the supported path.
- **Catalog flags**: CASP `AllowForRandom` uses the officially documented
  partFlags bit 2. COBJ flag-word semantics are partially undocumented;
  the patcher exposes configurable word/bit targets (CASP conventions by
  default) and preserves every other byte.
- **Live bridge** binds loopback only and requires a shared token; `exec`
  is disabled unless explicitly allowed on both sides.

## Tests

```bash
uv run pytest -q          # 86 tests
uv run pytest -v tests/test_dbpf.py
```

Coverage: FNV known answers, DBPF round-trips across flag combinations,
STBL v5 + 18 locales, SimData codec + compilers, tuning FTS + graph,
gatekeeper matrices, pyc headers, injector templates, packager,
CASP/COBJ/OBJD patching, and every MCP tool (in-process client included).
