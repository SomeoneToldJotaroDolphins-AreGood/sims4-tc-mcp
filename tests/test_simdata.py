"""SimData 0x101 codec and curated compiler tests."""

import struct

import pytest
from lxml import etree

from sims4_mcp.simdata.compilers import (compile_simdata,
                                         simdata_tgi_for,
                                         supported_classes)
from sims4_mcp.simdata.simdata import (SIMDATA_VERSION, SimDataField,
                                       SimDataFile, SimDataTable,
                                       SimDataType, build_simdata,
                                       parse_simdata)


def _table() -> SimDataTable:
    return SimDataTable(
        name="TraitMappings", tdesc_instance=0x1234,
        fields=[
            SimDataField("trait_id", SimDataType.UINT32, is_index=True),
            SimDataField("trait_name", SimDataType.STRING),
            SimDataField("trait_points", SimDataType.INT32),
            SimDataField("trait_ratio", SimDataType.FLOAT32),
        ],
        rows=[[1001, "trait_evil", -1, 0.5],
              [1002, "trait_kind", 1, 2.25]])


def test_roundtrip():
    blob = build_simdata(SimDataFile(tables=[_table()]))
    back = parse_simdata(blob)
    assert len(back.tables) == 1
    t = back.tables[0]
    assert t.rows[0][0] == 1001
    assert t.rows[0][1] == "trait_evil"
    assert t.rows[0][2] == -1
    assert abs(t.rows[0][3] - 0.5) < 1e-6
    assert abs(t.rows[1][3] - 2.25) < 1e-6


def test_header_version():
    blob = build_simdata(SimDataFile(tables=[_table()]))
    assert struct.unpack("<I", blob[:4])[0] == SIMDATA_VERSION == 0x101


def test_multiple_tables():
    second = SimDataTable(
        name="Buffs", tdesc_instance=0x99,
        fields=[SimDataField("buff_id", SimDataType.UINT32, True),
                SimDataField("buff_name", SimDataType.STRING)],
        rows=[[1, "Happy"]])
    back = parse_simdata(build_simdata(
        SimDataFile(tables=[_table(), second])))
    assert len(back.tables) == 2
    assert back.tables[1].rows == [[1, "Happy"]]


def test_rejects_bad_version():
    with pytest.raises(ValueError):
        parse_simdata(struct.pack("<II", 0x999, 0))


def test_trait_compiles_two_tables():
    xml = (
        '<Trait xmlns="http://www.maxis.com/schema/xmlTuning" '
        'xmlns:i="http://www.nra3d.com/Sims" '
        'i:id="0x800000000000ABCD" n="my_trait" trait_id="0x1234" '
        'trait_name="0x8000AAAA" trait_description="0x8000BBBB" '
        'trait_icon="icon_x" trait_points="3"/>')
    tables = compile_simdata(etree.fromstring(xml))
    names = [t.name for t in tables]
    assert "TraitMappings" in names
    assert "TraitDescriptions" in names
    mapping = next(t for t in tables if t.name == "TraitMappings")
    assert mapping.rows[0][0] == 0x1234
    desc = next(t for t in tables if t.name == "TraitDescriptions")
    assert desc.rows[0][1] == "0x8000AAAA"


def test_buff_compiles():
    xml = ('<Buff xmlns="http://www.maxis.com/schema/xmlTuning" '
           'xmlns:i="http://www.nra3d.com/Sims" i:id="0x42" '
           'n="my_buff" buff_name="0x8000CCCC" description="0x8000DDDD"/>')
    tables = compile_simdata(etree.fromstring(xml))
    assert [t.name for t in tables] == ["Buffs"]
    assert tables[0].rows[0][0] == 0x42


def test_simdata_tgi():
    t = _table()
    instance, group = simdata_tgi_for(t, group=0x1C000000)
    assert instance >> 31 == 1
    assert group == 0x1C000000


def test_supported_classes_nonempty():
    classes = supported_classes()
    for expected in ("Trait", "Buff"):
        assert expected in classes
