"""``sims4-tc-mcp`` FastMCP server.

Exposes 15 strongly-typed tools for building, inspecting, packaging and
debugging The Sims 4 script mods and total conversions.

Run with ``sims4-tc-mcp`` (stdio transport) or
``uv run mcp dev src/sims4_mcp/server.py``.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Literal, Optional

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from . import config
from .catalog.patcher import batch_create_catalog_overrides as _batch_overrides
from .catalog.patcher import inspect_catalog_resource
from .crypto.fnv import format_hash
from .dbpf.package import PackageError, parse_package, read_package
from .live_bridge.bridge import bridge_command as _bridge_command
from .live_bridge.diagnostics import analyze_last_exception as _analyze
from .models import TGI, TYPE_CASP, TYPE_COBJ, TYPE_OBJD, TYPE_SIMDATA, TYPE_STBL
from .project import compile_tuning as _compile_tuning
from .project import build_project, write_project_package
from .pyengine import astindex, injector, packager
from .pyengine.gatekeeper import check_source
from .simdata.simdata import parse_simdata
from .stbl.dictionary import create_dictionary
from .stbl.stbl import parse_stbl
from .tdesc import tdesc
from .tuning import indexer
from .tuning.indexer import build_index as _build_tuning_index

mcp = MCPServer(
    "sims4-tc-mcp",
    instructions=(
        "Tools for The Sims 4 script mods and total conversions: FNV "
        "hashing, DBPF .package I/O, TDESC schemas, SimData compilation, "
        "vanilla tuning search + dependency graph, game Python API search, "
        "Python 3.7 validation/packaging, catalog flag patching, live "
        "bridge and crash diagnostics."),
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

_JSON_TYPES = (dict, list, str, int, float, bool, type(None))


def _dumps(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _entry_summary(entry, decode: bool = True) -> dict:
    tgi = entry.tgi
    info: dict = {
        "tgi": str(tgi),
        "type": tgi.type_name,
        "compressed_size": entry.compressed_size,
        "file_size": entry.file_size,
        "compressed": entry.compressed_size != entry.file_size,
        "name": entry.name,
    }
    if not decode:
        return info
    try:
        if tgi.type == TYPE_STBL:
            stbl = parse_stbl(entry.data)
            info["stbl_language"] = stbl.language
            info["stbl_strings"] = len(stbl.entries)
            sample = stbl.entries[0].text if stbl.entries else ""
            info["stbl_sample"] = sample[:80]
        elif tgi.is_tuning:
            from lxml import etree
            root = etree.fromstring(entry.data)
            info["tuning_class"] = etree.QName(root).localname
            info["tuning_name"] = root.get("n")
            info["string_key"] = root.get("s")
        elif tgi.type in (TYPE_CASP, TYPE_COBJ, TYPE_OBJD):
            info["catalog"] = inspect_catalog_resource(entry.data, tgi.type)
    except Exception as e:  # noqa: BLE001 - summaries are best-effort
        info["decode_error"] = f"{type(e).__name__}: {e}"
    return info


def _decode_resource(tgi: TGI, data: bytes) -> dict:
    """Return a JSON-safe, human-readable payload for one resource."""
    if tgi.type == TYPE_STBL:
        stbl = parse_stbl(data)
        return {"format": "STBL", "language": stbl.language,
                "strings": stbl.as_dict()}
    if tgi.type == TYPE_SIMDATA:
        sim = parse_simdata(data)
        return {"format": "SimData",
                "tables": [t.as_dict() for t in sim.tables]}
    if tgi.type in (TYPE_CASP, TYPE_COBJ, TYPE_OBJD):
        return {"format": "Catalog",
                "data": inspect_catalog_resource(data, tgi.type)}
    if tgi.is_tuning:
        try:
            return {"format": "TuningXML",
                    "xml": data.decode("utf-8", errors="replace")}
        except UnicodeDecodeError:
            pass
    if data.lstrip().startswith(b"<"):
        return {"format": "XML", "xml": data.decode("utf-8",
                                                    errors="replace")}
    preview = base64.b64encode(data[:256]).decode("ascii")
    return {"format": "Binary", "size": len(data),
            "base64_preview": preview}


# ---------------------------------------------------------------------------
# 1. hash_fnv
# ---------------------------------------------------------------------------

@mcp.tool()
def hash_fnv(text: str, bits: Literal[24, 32, 64] = 64,
             high_bit: bool = True) -> dict:
    """Hash a string with FNV-1 (Sims 4 resource/tuning convention).

    Returns decimal, hex and TGI-instance representations.  STBL keys use
    bits=32, high_bit=True; custom tuning instances use bits=64.
    """
    return format_hash(text, bits, high_bit).as_dict()


# ---------------------------------------------------------------------------
# 2. inspect_package
# ---------------------------------------------------------------------------

@mcp.tool()
def inspect_package(package_path: str) -> dict:
    """List every TGI entry in a .package with type, sizes and metadata."""
    pkg = read_package(package_path)
    entries = [_entry_summary(e) for e in pkg.entries]
    return {"package": package_path, "index_version": pkg.index_version,
            "entry_count": len(pkg.entries), "entries": entries}


# ---------------------------------------------------------------------------
# 3. extract_package_resource
# ---------------------------------------------------------------------------

@mcp.tool()
def extract_package_resource(package_path: str, tgi: str,
                             output_path: Optional[str] = None) -> dict:
    """Extract one resource by TGI and decode it (XML/STBL/SimData/catalog)."""
    key = TGI.from_string(tgi)
    pkg = read_package(package_path)
    entry = pkg.find(key)
    if entry is None:
        return {"found": False, "tgi": str(key),
                "available": [str(e.tgi) for e in pkg.entries[:50]]}
    decoded = _decode_resource(key, entry.data)
    result = {"found": True, "tgi": str(key), "type": key.type_name,
              "size": len(entry.data), "decoded": decoded}
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(decoded.get("xml"), str):
            out.write_text(decoded["xml"], encoding="utf-8")
        else:
            out.write_bytes(entry.data)
        result["output_path"] = str(out)
    return result


# ---------------------------------------------------------------------------
# 4. build_package
# ---------------------------------------------------------------------------

@mcp.tool()
def build_package(output_package_path: str, project_dir: str) -> dict:
    """Compile a project dir (XML + .stbl.json + .bin/.tgi) into a package.

    Tuning XML gets companion SimData automatically when its class needs
    client-binary data.
    """
    report = write_project_package(project_dir, output_package_path)
    result = report.as_dict()
    result["output_package"] = output_package_path
    result["size"] = Path(output_package_path).stat().st_size
    return result


# ---------------------------------------------------------------------------
# 5. create_stbl_dictionary
# ---------------------------------------------------------------------------

@mcp.tool()
def create_stbl_dictionary(strings: list[dict], base_name: str,
                           generate_all_locales: bool = True,
                           output_dir: Optional[str] = None) -> dict:
    """Hash string references and write STBL resources (all 18 locales).

    Each item: {"key": "mod:thing.name", "text": "Display Name",
    "locale": "en_us"} (locale optional).
    """
    return create_dictionary(strings, base_name, generate_all_locales,
                             output_dir)


# ---------------------------------------------------------------------------
# 6. query_tdesc_schema
# ---------------------------------------------------------------------------

@mcp.tool()
def query_tdesc_schema(class_name: str) -> dict:
    """Return the tunable schema + starter XML for a tuning class."""
    schema = tdesc.query_class(class_name)
    if schema is None:
        return {"found": False, "class": class_name,
                "hint": "Set SIMS4_TDESC_DIR and run the index, or use a "
                        "bundled class: Trait, Buff, SuperInteraction, "
                        "LootActions, TestSetInstance"}
    return {"found": True, "schema": schema,
            "xml_skeleton": tdesc.xml_skeleton(class_name)}


# ---------------------------------------------------------------------------
# 7-9. Tuning index tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_vanilla_tuning(query: str,
                          class_filter: Optional[str] = None,
                          limit: int = 20) -> dict:
    """Full-text search over the indexed Maxis tuning XML database."""
    status = indexer.index_status()
    results = indexer.search(query, class_filter, limit)
    return {"index": status, "query": query, "results": results,
            "count": len(results)}


@mcp.tool()
def read_vanilla_tuning(instance_id: int) -> dict:
    """Return the complete XML of an indexed vanilla tuning."""
    result = indexer.read_tuning(instance_id)
    if result is None:
        return {"found": False, "instance": f"0x{instance_id:016X}",
                "hint": "Build the index with search_vanilla_tuning first "
                        "or check the instance id"}
    result["found"] = True
    return result


@mcp.tool()
def get_tuning_dependency_graph(
        instance_id: int,
        direction: Literal["incoming", "outgoing", "both"] = "both",
        depth: int = 2) -> dict:
    """Cross-tuning reference graph (what depends on / is used by a tuning)."""
    graph = indexer.dependency_graph(instance_id, direction, depth)
    graph["direction"] = direction
    return graph


# ---------------------------------------------------------------------------
# 10. compile_tuning_with_simdata
# ---------------------------------------------------------------------------

@mcp.tool()
def compile_tuning_with_simdata(xml_content: str,
                                output_dir: str) -> dict:
    """Validate tuning XML and compile matching 0x545AC67A SimData."""
    report = _compile_tuning(xml_content.encode("utf-8"))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    tuning_info = report.tuning[0]
    xml_path = out / f"{tuning_info.name or format(tuning_info.instance, '016X')}.xml"
    xml_path.write_text(xml_content, encoding="utf-8")
    written.append({"kind": "tuning", "tgi": str(tuning_info.tgi),
                    "path": str(xml_path)})
    for entry in report.entries[1:]:
        bin_path = out / f"{entry.tgi.instance:016X}_{entry.tgi.type:08X}.bin"
        bin_path.write_bytes(entry.data)
        sidecar = bin_path.with_suffix(".tgi")
        sidecar.write_text(str(entry.tgi), encoding="utf-8")
        written.append({"kind": "simdata", "tgi": str(entry.tgi),
                        "path": str(bin_path)})
    return {"tuning": {"class": tuning_info.class_name,
                       "tgi": str(tuning_info.tgi),
                       "instance": f"0x{tuning_info.instance:016X}"},
            "simdata_tables": report.simdata_tables,
            "files": written, "warnings": report.warnings}


# ---------------------------------------------------------------------------
# 11. search_game_python_api
# ---------------------------------------------------------------------------

@mcp.tool()
def search_game_python_api(query: str, limit: int = 25) -> dict:
    """Search decompiled Maxis Python classes, methods and docstrings."""
    res = astindex.search(query, limit)
    res["query"] = query
    res["status"] = ("ok" if config.decompiled_scripts_dir()
                     else "unconfigured")
    return res


# ---------------------------------------------------------------------------
# 12. generate_runtime_tuning_hook
# ---------------------------------------------------------------------------

@mcp.tool()
def generate_runtime_tuning_hook(hook_type: str,
                                 config_json: str) -> dict:
    """Generate Python 3.7 injection/stripping code for runtime hooks.

    hook_type: add_interaction | remove_interaction |
    add_autonomy_commodity | start_buff | filter_super_affordances.
    """
    source = injector.generate_hook_module(hook_type, config_json)
    check = check_source(source)
    return {"hook_type": hook_type, "compatible": check.compatible,
            "issues": check.as_dict()["issues"], "source": source}


# ---------------------------------------------------------------------------
# 13. validate_and_build_ts4script
# ---------------------------------------------------------------------------

@mcp.tool()
def validate_and_build_ts4script(source_dir: str, output_path: str,
                                 module_name: str = "mod",
                                 strict: bool = True) -> dict:
    """Run the Python 3.7 AST gatekeeper then bundle a .ts4script zip."""
    result = packager.build_ts4script(source_dir, output_path,
                                      module_name=module_name,
                                      strict=strict)
    return result.as_dict()


# ---------------------------------------------------------------------------
# 14. batch_create_catalog_overrides
# ---------------------------------------------------------------------------

@mcp.tool()
def batch_create_catalog_overrides(
        resource_type: Literal["casp", "cobj", "objd"],
        filter_query: str,
        disable_random_spawn: bool,
        output_package: str,
        source_packages: Optional[list[str]] = None,
        source_dir: Optional[str] = None,
        hide_items: bool = False) -> dict:
    """Bulk-patch CASP/COBJ/OBJD catalog flags into an override package.

    When no sources are given, ``SIMS4_MODS_DIR`` is scanned.
    """
    if not source_packages and not source_dir:
        src = config.mods_dir()
        if src is None:
            return {"error": "no source_packages/source_dir given and "
                             "SIMS4_MODS_DIR is not configured"}
        source_dir = str(src)
    result = _batch_overrides(
        resource_type=resource_type, filter_query=filter_query,
        disable_random_spawn=disable_random_spawn,
        output_package=output_package,
        source_packages=source_packages, source_dir=source_dir,
        hide_items=hide_items)
    return result.as_dict()


# ---------------------------------------------------------------------------
# 15. analyze_last_exception
# ---------------------------------------------------------------------------

@mcp.tool()
def analyze_last_exception(sims4_docs_dir: Optional[str] = None) -> dict:
    """Parse the newest lastException/lastUIException/lastCrash file and
    cross-reference it with the game Python and tuning indexes."""
    return _analyze(sims4_docs_dir)


# ---------------------------------------------------------------------------
# Extra: live bridge + index maintenance
# ---------------------------------------------------------------------------

@mcp.tool()
def bridge_send_command(host: str, port: int, command: str,
                        args_json: str = "{}", token: str = "",
                        allow_exec: bool = False) -> dict:
    """Send a command to a running game via the live bridge .ts4script."""
    args = json.loads(args_json) if args_json else {}
    return _bridge_command(host, port, command, args, token=token,
                           allow_exec=allow_exec).as_dict()


@mcp.tool()
def rebuild_indexes(tuning: bool = True, python_api: bool = True,
                    tdesc: bool = True, force: bool = False) -> dict:
    """(Re)build the vanilla tuning, game Python and TDESC indexes."""
    result: dict = {}
    if tuning:
        result["tuning"] = _build_tuning_index(force=force)
    if python_api:
        result["python_api"] = astindex.build_index(force=force)
    if tdesc:
        result["tdesc"] = {"classes": tdesc.build_index(force=force)}
    return result


# ---------------------------------------------------------------------------
# Resources & prompt
# ---------------------------------------------------------------------------

@mcp.resource("sims4://config")
def config_resource() -> str:
    """Resolved environment paths and cache locations."""
    return _dumps(config.status())


@mcp.resource("sims4://indexes")
def indexes_resource() -> str:
    """Status of the tuning and game-Python indexes."""
    return _dumps({
        "tuning": indexer.index_status(),
        "python_api_dir": str(config.decompiled_scripts_dir()
                              or "unconfigured"),
        "tdesc_dir": str(config.tdesc_dir() or "unconfigured"),
    })


@mcp.prompt()
def total_conversion_workflow(mod_name: str, creator: str = "creator") -> str:
    """Guide an agent through a total-conversion build."""
    return (
        f"Build the Sims 4 total conversion '{mod_name}' by {creator}.\n\n"
        "1. query_tdesc_schema for every tuning class you plan to use; note "
        "which need client SimData.\n"
        "2. search_vanilla_tuning to find vanilla analogues; "
        "get_tuning_dependency_graph before removing anything.\n"
        "3. Write tuning XML under <project>/tuning/. Use "
        "compile_tuning_with_simdata to validate each file.\n"
        "4. create_stbl_dictionary for UI strings; reference the returned "
        "0x keys in the XML.\n"
        "5. Put binary resources under <project>/bin/ with .tgi sidecars.\n"
        "6. build_package(<project>, <out.package>) and verify with "
        "inspect_package.\n"
        "7. For scripts: generate_runtime_tuning_hook, write modules into "
        "<src>/, then validate_and_build_ts4script.\n"
        "8. If the game errors, analyze_last_exception and correlate with "
        "search_game_python_api.\n"
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
