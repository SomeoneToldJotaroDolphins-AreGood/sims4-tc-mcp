"""Shared test fixtures: isolated cache + synthetic resources."""

from __future__ import annotations

import os
import struct

import pytest

from sims4_mcp.crypto.fnv import fnv64


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("SIMS4_MCP_CACHE_DIR", str(cache))
    cache.mkdir(parents=True, exist_ok=True)
    yield cache


def make_casp(name: str = "ymShoes_Test", allow_random: bool = True,
              version: int = 0x20) -> bytes:
    """Synthetic CASP v0x20 per the Maxis layout."""
    name_b = name.encode("utf-16-be")
    body = struct.pack("<I", 0)
    body += struct.pack("<I", len(name)) + name_b
    body += struct.pack("<f", 1.5) + struct.pack("<H", 0)
    body += struct.pack("<I", 7) + struct.pack("<I", 0)
    body += struct.pack("<B", 0x04 if allow_random else 0x00)
    body += struct.pack("<Q", 0) + struct.pack("<I", 0)
    body += struct.pack("<I", 0)          # tagCount
    body += struct.pack("<I", 100)        # simoleonPrice
    body += struct.pack("<I", 0) + struct.pack("<I", 0)  # title/desc keys
    body += struct.pack("<B", 0)          # uniqueTextureSpace
    body += struct.pack("<i", 5)          # bodyType
    body += struct.pack("<i", 8)          # unused1
    body += struct.pack("<I", 0x1078)     # ageGender
    body += struct.pack("<B", 0)          # keyCount
    return struct.pack("<II", version, len(body)) + body


def make_cobj(name_hash: int = 0x12345678, price: int = 500,
              category_flags: int = 0) -> bytes:
    """Synthetic COBJ v0x19 with one empty common block."""
    out = struct.pack("<I", 0x19)
    out += struct.pack("<I", 0x09)
    out += struct.pack("<III", name_hash, 0, price)
    out += struct.pack("<III", 0, 0, 0)   # unkCommon01..03
    out += struct.pack("<B", 0)           # styleCount
    out += struct.pack("<H", 0)           # unkCommon04
    out += struct.pack("<I", 0)           # tagCount
    out += struct.pack("<I", 0)           # sellingPointCount
    out += struct.pack("<Q", 0) + struct.pack("<H", 0) + struct.pack("<Q", 0)
    out += struct.pack("<I", 0) * 7       # unk01, hash01..03, unk02,
                                          # hash04, hash05
    out += struct.pack("<I", 0)           # unkFlags01
    out += struct.pack("<I", category_flags)
    out += struct.pack("<I", 0)           # unkFlags03
    out += struct.pack("<I", 0)           # unkFlags04
    out += struct.pack("<I", 0)           # placementFlags
    out += struct.pack("<Q", 0) + struct.pack("<B", 0)
    out += struct.pack("<Q", 0) + struct.pack("<B", 0)
    out += struct.pack("<B", 0)           # colorCount
    out += struct.pack("<B", 0) * 5       # unk05..unk09
    out += struct.pack("<B", 1)           # buildbuy = buy
    out += struct.pack("<I", 0) * 4       # v0x19 trailing
    return out


def make_trait_xml(instance: int = 0x800000000000ABCD,
                   name: str = "tc_test_trait") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Trait xmlns="http://www.maxis.com/schema/xmlTuning" '
        'xmlns:i="http://www.nra3d.com/Sims" '
        f'i:id="0x{instance:016X}" n="{name}" s="{name}" '
        'trait_id="0x1234" trait_name="0x8000ABCD" '
        'trait_description="0x8000ABCE" trait_icon="icon_test" '
        'trait_points="2"/>'
    ).encode("utf-8")
