"""MCP tool-level tests (direct calls + in-process server round-trip)."""

import asyncio
import json
from pathlib import Path

import pytest

from sims4_mcp import server
from sims4_mcp.dbpf.package import PackageEntry, build_package
from sims4_mcp.models import TGI, TYPE_CASP, TYPE_STBL

from .conftest import make_casp, make_trait_xml

REQUIRED_TOOLS = {
    "hash_fnv", "inspect_package", "extract_package_resource",
    "build_package", "create_stbl_dictionary", "query_tdesc_schema",
    "search_vanilla_tuning", "read_vanilla_tuning",
    "get_tuning_dependency_graph", "compile_tuning_with_simdata",
    "search_game_python_api", "generate_runtime_tuning_hook",
    "validate_and_build_ts4script", "batch_create_catalog_overrides",
    "analyze_last_exception",
}


def _payload(result) -> dict:
    """Normalize MCPServer call results to structured data."""
    if result.structured_content:
        sc = result.structured_content
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


def test_all_15_required_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert REQUIRED_TOOLS <= names
    assert len(names) >= 15


def test_in_process_tool_roundtrip():
    result = asyncio.run(server.mcp.call_tool(
        "hash_fnv", {"text": "Sims4", "bits": 32, "high_bit": True}))
    data = _payload(result)
    assert data["bits"] == 32
    assert int(data["hex"], 16) >> 31 == 1


def test_hash_fnv_tool():
    d = server.hash_fnv("sims4modding", 64, True)
    assert d["hex"].startswith("0x")
    assert int(d["dec"]) == int(d["hex"], 16)


def test_inspect_and_extract(tmp_path):
    pkg_path = tmp_path / "test.package"
    pkg_path.write_bytes(build_package([
        PackageEntry(TGI(type=0xE882D22F, group=0x1C000000, instance=0x80000000000000AA),
                     make_trait_xml(instance=0x80000000000000AA)),
        PackageEntry(TGI(type=TYPE_CASP, group=0x1C000000, instance=0x80000000000000BB),
                     make_casp("ymShoes_X")),
    ]))
    info = server.inspect_package(str(pkg_path))
    assert info["entry_count"] == 2
    types = {e["type"] for e in info["entries"]}
    assert any("Tuning" in t for t in types)
    assert "CASP" in types

    tgi = str(TGI(type=0xE882D22F, group=0x1C000000, instance=0x80000000000000AA))
    out = tmp_path / "tuning.xml"
    ext = server.extract_package_resource(str(pkg_path), tgi, str(out))
    assert ext["found"] and ext["decoded"]["format"] == "TuningXML"
    assert out.read_text().startswith("<?xml")

    missing = server.extract_package_resource(
        str(pkg_path), "0x03B33DDF-0x1C000000-0x80000000000000FF")
    assert missing["found"] is False


def test_extract_stbl(tmp_path):
    from sims4_mcp.stbl.stbl import (StblEntry, StblFile, build_stbl,
                                     make_key)
    blob = build_stbl(StblFile(entries=[
        StblEntry(key=make_key("m:x"), text="Hello")]))
    pkg_path = tmp_path / "stbl.package"
    pkg_path.write_bytes(build_package([
        PackageEntry(TGI(type=TYPE_STBL, group=0xFFFFFFFF, instance=0x0000000000000001), blob)]))
    out = server.extract_package_resource(
        str(pkg_path), "0x220557DA-0xFFFFFFFF-0x0000000000000001")
    assert out["decoded"]["strings"][f"0x{make_key('m:x'):08X}"] == "Hello"


def test_build_package_tool(tmp_path):
    project = tmp_path / "project"
    (project / "tuning").mkdir(parents=True)
    (project / "tuning" / "trait.xml").write_bytes(make_trait_xml())
    (project / "strings.stbl.json").write_text(json.dumps({
        "base_name": "tcmod",
        "locales": {"en_us": {"tcmod:trait.name": "Test Trait"}}}))
    out = tmp_path / "out.package"
    result = server.build_package(str(out), str(project))
    assert result["entry_count"] >= 2
    assert result["simdata_tables"]
    assert out.exists()

    inspected = server.inspect_package(str(out))
    assert inspected["entry_count"] == result["entry_count"]


def test_create_stbl_dictionary_tool(tmp_path):
    result = server.create_stbl_dictionary(
        [{"key": "tcmod:item.name", "text": "Item"},
         {"key": "tcmod:item.desc", "text": "Desc", "locale": "de_de"}],
        "tcmod", True, str(tmp_path))
    assert len(result["files"]) == 18
    assert result["keys"]["tcmod:item.name"].startswith("0x")


def test_query_tdesc_schema_tool():
    found = server.query_tdesc_schema("Trait")
    assert found["found"] is True
    assert "trait_id" in {f["name"] for f in found["schema"]["fields"]}
    assert "<Trait" in found["xml_skeleton"]
    assert server.query_tdesc_schema("DoesNotExist")["found"] is False


def test_search_and_read_tuning_tools(tmp_path, monkeypatch):
    corpus = tmp_path / "tuning"
    corpus.mkdir()
    (corpus / "buff.xml").write_text(
        '<Buff xmlns="http://www.maxis.com/schema/xmlTuning" '
        'xmlns:i="http://www.nra3d.com/Sims" n="buff_xyz" s="buff_xyz" '
        'i:id="0xABC"/>')
    monkeypatch.setenv("SIMS4_EXTRACTED_TUNING_DIR", str(corpus))
    from sims4_mcp.tuning import indexer
    indexer.build_index()

    search = server.search_vanilla_tuning("buff_xyz")
    assert search["count"] == 1
    read = server.read_vanilla_tuning(0xABC)
    assert read["found"] and read["name"] == "buff_xyz"
    graph = server.get_tuning_dependency_graph(0xABC, "both")
    assert graph["root"]["name"] == "buff_xyz"


def test_compile_tuning_with_simdata_tool(tmp_path):
    xml = make_trait_xml().decode()
    result = server.compile_tuning_with_simdata(xml, str(tmp_path))
    assert result["simdata_tables"]
    kinds = {f["kind"] for f in result["files"]}
    assert kinds == {"tuning", "simdata"}
    for f in result["files"]:
        assert Path(f["path"]).exists()


def test_generate_runtime_tuning_hook_tool():
    result = server.generate_runtime_tuning_hook(
        "add_interaction", '{"interaction_ids": [1, 2]}')
    assert result["compatible"] is True
    assert "def inject" in result["source"]


def test_validate_and_build_ts4script_tool(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def _on_load():\n    return 1\n")
    out = tmp_path / "mod.ts4script"
    result = server.validate_and_build_ts4script(str(src), str(out))
    assert result["compatible"] and out.exists()


def test_unconfigured_indexes_are_graceful(monkeypatch):
    for var in ("SIMS4_EXTRACTED_TUNING_DIR", "SIMS4_DECOMPILED_SCRIPTS_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert server.search_game_python_api("Object")["status"] == \
        "unconfigured"
    assert server.search_vanilla_tuning("x")["index"]["indexed"] in \
        (True, False)


def test_analyze_exception_tool(tmp_path):
    (tmp_path / "lastException.txt").write_text(
        "Traceback (most recent call last):\n"
        '  File "T:\\Mods\\mymod.py", line 10, in _on_load\n'
        "AttributeError: boom\n")
    result = server.analyze_last_exception(str(tmp_path))
    assert result["found"] is True
    assert result["exception_type"] == "AttributeError"
    assert result["frames"][0]["function"] == "_on_load"


def test_config_resource():
    data = json.loads(server.config_resource())
    assert "cache_dir" in data
