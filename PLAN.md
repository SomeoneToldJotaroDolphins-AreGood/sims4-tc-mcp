# sims4-tc-mcp — Implementation Plan

## Phase 0 — Scaffold & Toolchain
- `pip install --user uv`; `uv init` in `/home/baris/Sims4Modding`
- Project `sims4-tc-mcp`, package `src/sims4_mcp/`
- `requires-python = ">=3.11"`, `uv python pin 3.12`
- Deps: `mcp[cli]`, `lxml`, `pydantic>=2`; dev: `pytest`
- Console script `sims4-tc-mcp = sims4_mcp.server:main`
- Shared `models.py`: frozen pydantic `TGI`, `TYPE_NAMES` registry
- `config.py`: env resolution for all SIMS4_* vars with graceful degradation

## Phase 1 — crypto/fnv.py
- FNV-1 (multiply→XOR), 24/32/64-bit, lowercase normalization, high_bit toggling
- `format_hash()` → {dec, hex, tgi_instance}
- Known-answer tests

## Phase 2 — dbpf/ (reader, writer, zlib)
- Reader: header, index v0–v3, all constant-encoding flags, name table, ZLIB/stored payloads
- Writer: index v3, auto-select flags, TGI-sorted, ZLIB with fallback
- Tests: synthetic round-trips across flag combinations

## Phase 3 — stbl/
- v5 parse/build: signature, version 5, format 1, entry table, null-terminated UTF-8
- 18-locale generator: instance = fnv32(base_name) | (locale_code << 56)
- Round-trip + locale-matrix tests

## Phase 4 — tdesc/
- Parse .tdesc XML: class name, fields, tunable primitives, lists, variants, tuples, enums
- Query engine: query_tdesc_schema(class_name) → XML skeleton + SimData flags
- SQLite cache over SIMS4_TDESC_DIR + bundled curated TDESCs for testing

## Phase 5 — simdata/ (0x545AC67A v0x101)
- Codec: header, table name (FNV32), TDESC class ref, row/column layout, typed cells
- Curated compilers: TraitMappings, TraitDescriptions, Buffs, AspirationMappings, Venue/Lot, Career, Objectives
- compile_tuning_with_simdata(xml, output_dir)
- Tests: codec round-trip, trait-table compilation

## Phase 6 — tuning/ (FTS5 index + reverse-ref graph)
- Indexer walks SIMS4_EXTRACTED_TUNING_DIR, lxml.iterparse → SQLite FTS5 + tuning_refs
- Incremental re-index via mtime manifest
- Tests against synthetic mini-corpus

## Phase 7 — pyengine/
- AST indexer: walk SIMS4_DECOMPILED_SCRIPTS_DIR → SQLite (classes, methods, singletons)
- 3.7.0 gatekeeper: reject walrus, match, positional-only, dict|, generic subscripts, f-string =
- pyc header writer (magic 3394)
- Runtime injector generator (InstanceManager hooks)
- Packager: validate → zip → .ts4script with mod_version.py

## Phase 8 — catalog/
- CASP (0x034AEECB) and COBJ/OBJD (0x319E4F1D) binary header parsers
- Flag patching (AllowForRandom, catalog-part flags)
- batch_create_catalog_overrides
- Tests with synthetic binaries

## Phase 9 — live_bridge/
- Generator: companion .ts4script with non-blocking TCP listener
- Client tool: bridge_command(host, port, command, payload)
- analyze_last_exception() cross-referencing Python API + tuning index

## Phase 10 — server.py (FastMCP)
- All 15 tools, strongly typed, docstrings as descriptions
- Resources (sims4://config, index status)
- Prompt: total-conversion-workflow
- main() = stdio transport

## Phase 11 — Tests, README, Verification
- Comprehensive pytest suites
- uv run pytest until green
- README.md with setup, env vars, opencode.jsonc snippet

## Key Decisions
- SimData: schema-driven + curated tables (extensible registry)
- pyc: gatekeeper + header-only writer (game compiles .py at runtime)
- Live bridge: script generator + outbound client tool
- Naming: project sims4-tc-mcp, package sims4_mcp
