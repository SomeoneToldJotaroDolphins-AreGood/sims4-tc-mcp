# TODO — sims4-tc-mcp

Status: 🔲 todo · 🔄 in progress · ✅ done

## Phase 0 — Scaffold & toolchain
- [x] Install uv, managed Python 3.12
- [x] `pyproject.toml` (deps: mcp[cli], lxml, pydantic; dev: pytest)
- [x] `src/sims4_mcp/` package layout (9 subpackages)
- [x] Console script `sims4-tc-mcp`
- [x] `models.py` (TGI, TYPE_NAMES registry)
- [x] `config.py` (env resolution, cache dir)

## Phase 1 — crypto/fnv.py
- [x] FNV-1 24/32/64 with lowercase + high-bit
- [x] `format_hash()` (dec / hex / tgi_instance)
- [x] Known-answer verification

## Phase 2 — dbpf/
- [x] Reader: header, index v0-3, constant flags, name table, ZLIB/stored
- [x] Writer: index v3, auto constant flags, TGI sort, ZLIB fallback
- [x] Round-trip verification (multi-entry, constant-type)

## Phase 3 — stbl/
- [x] STBL v5 parser + builder
- [x] 18-locale generator + instance IDs
- [x] Round-trip + locale verification

## Phase 4 — tdesc/
- [x] `.tdesc` XML parser (fields, export modes, enums)
- [x] SQLite cache + bundled fallback registry
- [x] `xml_skeleton()` renderer

## Phase 5 — simdata/
- [x] 0x101 binary codec (tables, typed cells, indexes, string pool)
- [x] Curated compilers (Trait, Buff, Aspiration) + registry
- [x] Round-trip + compile verification

## Phase 6 — tuning/
- [x] FTS5 index over extracted XML
- [x] Reverse-reference graph (tuning_refs)
- [x] Incremental mtime rebuild
- [x] search / read / dependency_graph verification

## Phase 7 — pyengine/
- [x] AST indexer (classes, MRO, methods, FTS)
- [x] Python 3.7.0 gatekeeper (all constructs + rewrites check)
- [x] pyc magic-3394 header writer
- [x] Runtime injector generator (InstanceManager hooks)
- [x] `.ts4script` packager (validate + zip + mod_version.py)

## Phase 8 — catalog/
- [x] CASP (0x034AEECB) header parser/patcher
- [x] COBJ/OBJD (0x319E4F1D / 0xC0DB5AE7) flag parser/patcher
- [x] `batch_create_catalog_overrides` pipeline

## Phase 9 — live_bridge/
- [x] Companion `.ts4script` TCP listener generator
- [x] Outbound client (`bridge_command`)
- [x] `analyze_last_exception` cross-referencing index

## Phase 10 — server.py
- [x] 15 FastMCP tools (+2 maintenance tools)
- [x] Resources (`sims4://config`, `sims4://indexes`)
- [x] `total-conversion-workflow` prompt
- [x] stdio `main()`

## Phase 11 — Tests, README, Verification
- [x] tests/test_fnv.py
- [x] tests/test_dbpf.py
- [x] tests/test_stbl.py
- [x] tests/test_simdata.py
- [x] tests/test_tuning.py
- [x] tests/test_pyengine.py
- [x] tests/test_catalog.py
- [x] tests/test_server_tools.py
- [x] `uv run pytest` all green (86 passed)
- [x] README.md (setup, env vars, opencode.jsonc)

## Phase 12 — Remote transport & one-click setup
- [x] `streamable-http` transport + bearer-token middleware
- [x] LAN bind refuses to start without `SIMS4_MCP_AUTH_TOKEN`
- [x] `tests/test_http_transport.py` (auth matrix, tool round-trip)
- [x] `sims4_mcp/setup.py`: Proton/EA/Lutris/Flatpak folder detection
- [x] Saved config (`~/.config/sims4-tc-mcp/config.json`) with env override
- [x] Token generation/0600 storage, free-port selection, LAN URL
- [x] systemd user unit + desktop launcher (`.desktop`)
- [x] `--tui` (rich menu), `--auto`, `--status`, `--print-client-config`
- [x] `tests/test_setup.py` (107 total tests)
