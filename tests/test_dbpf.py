"""DBPF 2.1 package round-trip and flag tests."""

import struct

import pytest

from sims4_mcp.dbpf.package import (FLAG_CONSTANT_GROUP, FLAG_CONSTANT_TYPE,
                                    FLAG_HAS_NAME_KEYS, MAGIC_ZLIB,
                                    PackageEntry, PackageError,
                                    build_package, parse_package)
from sims4_mcp.models import TGI

XML = (b'<?xml version="1.0"?><Buff xmlns='
       b'"http://www.maxis.com/schema/xmlTuning" n="test" s="test">'
       + b'<T n="padding" value="0x1234567890"/>' * 40 + b"</Buff>")
BLOB = bytes(range(256)) * 8


def _mk(t=0x03B33DDF, g=0x1C000000, i=0x8000000000000001, data=XML,
        name=None):
    return PackageEntry(TGI(type=t, group=g, instance=i), data, name=name)


def test_roundtrip_mixed_entries():
    entries = [
        _mk(data=XML, name="tuning_one"),
        _mk(t=0x220557DA, g=0xFFFFFFFF, i=0x0000000012345678, data=BLOB),
        _mk(t=0x220557DA, g=0xFFFFFFFF, i=0x0000000012345679, data=b"tiny"),
    ]
    pkg = parse_package(build_package(entries))
    assert len(pkg.entries) == 3
    by_tgi = {e.tgi: e for e in pkg.entries}
    for original in entries:
        got = by_tgi[original.tgi]
        assert got.data == original.data
        assert got.name == original.name


def test_compression_flags_and_sizes():
    pkg = parse_package(build_package([_mk()]))
    entry = pkg.entries[0]
    # XML is repetitive -> compressed; stored size < file size
    assert entry.compressed_size < entry.file_size


def test_small_payload_stays_uncompressed():
    pkg = parse_package(build_package([_mk(data=b"tiny")]))
    entry = pkg.entries[0]
    assert entry.compressed_size == entry.file_size == 4


def test_constant_type_and_group_flags_present():
    entries = [_mk(i=0x8000000000000000 + i) for i in range(6)]
    blob = build_package(entries)
    index_off = struct.unpack("<I", blob[-4:])[0]
    flags = struct.unpack("<I", blob[index_off:index_off + 4])[0]
    assert flags & FLAG_CONSTANT_TYPE
    assert flags & FLAG_CONSTANT_GROUP


def test_name_table_roundtrip():
    entries = [_mk(i=0x8000000000000010, name="alpha"),
               _mk(i=0x8000000000000011, name="beta")]
    blob = build_package(entries)
    index_off = struct.unpack("<I", blob[-4:])[0]
    flags = struct.unpack("<I", blob[index_off:index_off + 4])[0]
    assert flags & FLAG_HAS_NAME_KEYS
    pkg = parse_package(blob)
    assert {e.name for e in pkg.entries} == {"alpha", "beta"}


def test_bad_magic_rejected():
    with pytest.raises(PackageError):
        parse_package(b"JUNK" + b"\x00" * 100)


def test_empty_package_rejected():
    with pytest.raises(PackageError):
        build_package([])


def test_64bit_instance_preserved():
    tgi = TGI(type=0x03B33DDF, group=0x1C000000,
              instance=0xA4FBFA885D2A931D)
    pkg = parse_package(build_package([_mk(i=tgi.instance)]))
    assert pkg.entries[0].tgi.instance == tgi.instance
