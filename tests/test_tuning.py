"""Tuning FTS5 index and dependency graph tests."""

import pytest

from sims4_mcp.tuning import indexer

BUFF_XML = """<Buff xmlns="http://www.maxis.com/schema/xmlTuning"
 xmlns:i="http://www.nra3d.com/Sims" n="buff_test" s="buff_test"
 i:id="0x111"><T n="duration" value="0x222"/></Buff>"""

INTER_XML = """<I xmlns="http://www.maxis.com/schema/xmlTuning"
 n="interaction_test" s="interaction_test"><T n="buff" value="0x111"/>
 <T n="loot" value="0x333"/></I>"""


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "tuning"
    root.mkdir()
    (root / "buff.xml").write_text(BUFF_XML)
    (root / "inter.xml").write_text(INTER_XML)
    monkeypatch.setenv("SIMS4_EXTRACTED_TUNING_DIR", str(root))
    return root


def test_build_index_counts_tunings(corpus):
    result = indexer.build_index()
    assert result["status"] == "ok"
    assert result["tunings_indexed"] == 2


def test_incremental_skips_unchanged(corpus):
    indexer.build_index()
    again = indexer.build_index()
    assert again["files_processed"] == 0
    forced = indexer.build_index(force=True)
    assert forced["files_processed"] == 2


def test_search_and_read(corpus):
    indexer.build_index()
    hits = indexer.search("buff_test")
    assert hits and hits[0]["instance"].endswith("0111")
    row = indexer.read_tuning(0x111)
    assert row["class"] == "Buff"
    assert "buff_test" in row["xml"]


def test_class_filter(corpus):
    indexer.build_index()
    assert indexer.search("test", class_filter="Buff")
    assert indexer.search("test", class_filter="LootActions") == []


def test_dependency_graph_both_directions(corpus):
    indexer.build_index()
    graph = indexer.dependency_graph(0x111, "both")
    outgoing = {e["to"] for e in graph["depends_on"]}
    incoming = {e["from"] for e in graph["depended_by"]}
    assert "0x0000000000000222" in outgoing
    assert incoming  # the interaction references the buff


def test_direction_filtering(corpus):
    indexer.build_index()
    only_out = indexer.dependency_graph(0x111, "outgoing")
    assert "depends_on" in only_out and "depended_by" not in only_out


def test_unconfigured_reports_hint(monkeypatch):
    monkeypatch.delenv("SIMS4_EXTRACTED_TUNING_DIR", raising=False)
    result = indexer.build_index()
    assert result["status"] == "unconfigured"
    assert "hint" in result
