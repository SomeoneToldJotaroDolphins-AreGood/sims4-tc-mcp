"""Catalog CASP/COBJ/OBJD patcher tests."""

import struct

import pytest

from sims4_mcp.catalog import casp, cobj, objd
from sims4_mcp.catalog.patcher import (batch_create_catalog_overrides,
                                       inspect_catalog_resource)
from sims4_mcp.dbpf.package import PackageEntry, build_package
from sims4_mcp.models import TGI, TYPE_CASP, TYPE_COBJ

from .conftest import make_casp, make_cobj


def test_casp_parse():
    info = casp.parse_casp(make_casp())
    assert info.name == "ymShoes_Test"
    assert info.flags()["AllowForRandom"] is True
    assert info.body_type == 5
    assert info.age_gender == 0x1078
    assert info.simoleon_price == 100


def test_casp_patch_in_place():
    data = bytearray(make_casp(allow_random=True))
    patched, detail = casp.patch_casp_flags(data, allow_for_random=False)
    info = casp.parse_casp(bytes(patched))
    assert info.flags()["AllowForRandom"] is False
    assert detail["changes"]["AllowForRandom"] == {"before": True,
                                                   "after": False}
    # every other byte preserved
    original = make_casp(allow_random=True)
    changed = [i for i, (a, b) in enumerate(zip(original, patched))
               if a != b]
    assert len(changed) == 1


def test_casp_noop_patch():
    data = bytearray(make_casp())
    patched, detail = casp.patch_casp_flags(data)
    assert detail["changes"] == {}
    assert bytes(patched) == bytes(data)


def test_casp_rejects_bad_version():
    with pytest.raises(casp.CaspError):
        casp.parse_casp(struct.pack("<II", 0x11, 0))


def test_cobj_parse_and_patch():
    obj = cobj.parse_cobj(make_cobj(category_flags=0x04))
    assert obj.price == 500
    assert obj.buildbuy == 1
    patched, detail = cobj.patch_cobj_flags(
        bytearray(make_cobj(category_flags=0x04)),
        allow_for_random=False)
    again = cobj.parse_cobj(bytes(patched))
    assert again.flag_values["categoryFlags"] == 0x00
    assert detail["changes"]["AllowForRandom"]["before"] is True


def test_objd_tlv_parse():
    # two entries: name string then price float
    payload1 = b"\x05\x00Hello\x00"      # 8 bytes
    payload2 = struct.pack("<f", 12.5)
    header = struct.pack("<HIH", 0, 8, 2)
    refs = struct.pack("<II", 0xE7F07786, 24) + \
        struct.pack("<II", 0xE4F4FAA4, 32)
    data = header + refs + payload1 + payload2
    info = objd.parse_objd(data)
    assert len(info.entries) == 2
    assert info.entries[1].type_name == "Price (FLOAT)"
    assert info.entries[1].payload == payload2


def test_inspect_catalog_dispatch():
    assert inspect_catalog_resource(make_casp(), TYPE_CASP)["name"] == \
        "ymShoes_Test"
    assert inspect_catalog_resource(make_cobj(), TYPE_COBJ)["price"] == 500


def test_batch_overrides_pipeline(tmp_path):
    pkg = build_package([
        PackageEntry(TGI(type=TYPE_CASP, group=0x1C000000, instance=0x8000000000000001),
                     make_casp("ymShoes_A", allow_random=True)),
        PackageEntry(TGI(type=TYPE_CASP, group=0x1C000000, instance=0x8000000000000002),
                     make_casp("ymShoes_B", allow_random=True)),
        PackageEntry(TGI(type=0x220557DA, group=0xFFFFFFFF, instance=0x1234), b"\x00" * 16),
    ])
    src = tmp_path / "source.package"
    src.write_bytes(pkg)

    result = batch_create_catalog_overrides(
        resource_type="casp", filter_query="ymShoes_A",
        disable_random_spawn=True,
        output_package=str(tmp_path / "override.package"),
        source_packages=[str(src)])
    assert result.patched == 1
    assert result.scanned == 3
    assert result.errors == []

    from sims4_mcp.dbpf.package import parse_package
    out = parse_package((tmp_path / "override.package").read_bytes())
    assert len(out.entries) == 1
    assert casp.parse_casp(out.entries[0].data).flags()[
        "AllowForRandom"] is False


def test_batch_overrides_bad_type(tmp_path):
    with pytest.raises(ValueError):
        batch_create_catalog_overrides(
            resource_type="nope", filter_query="", disable_random_spawn=True,
            output_package=str(tmp_path / "x.package"),
            source_packages=["/does/not/exist.package"])
