"""COBJ (Catalog Object, ``0x319E4F1D``) parser and flag patcher.

Layout from the community wiki (version 0x19)::

    DWORD version
    -- common block --
    DWORD commonBlockVersion (0x09)
    DWORD nameHash
    DWORD descriptionHash
    DWORD price
    DWORD unkCommon01..03
    BYTE  styleCount; Style[styleCount] (QWORD iid, DWORD tid, DWORD gid)
    WORD  unkCommon04
    DWORD tagCount; WORD tag[tagCount]
    DWORD sellingPointCount; (WORD commodity, DWORD amount)
    QWORD unkCommon05
    WORD  unkCommon06
    QWORD unkCommon07
    -- trailing catalog block --
    DWORD unk01, hash01..03, unk02, hash04, hash05
    DWORD unkFlags01
    DWORD categoryFlags
    DWORD unkFlags03
    DWORD unkFlags04
    DWORD placementFlags
    QWORD unkIID01
    BYTE  unk03
    QWORD unkIID02
    BYTE  unk04
    BYTE  colorCount; DWORD colors[colorCount]
    BYTE  unk05..unk09
    BYTE  buildbuy        (0x00 build, 0x01 buy)
    if version == 0x19: 4 trailing DWORDs

The Maxis documentation leaves the flag-word semantics unnamed.  We
therefore expose **named presets** with explicit, configurable bit
locations rather than hard-coding guesses.  The default
``allow_for_random`` preset targets bit 2 of ``categoryFlags`` — the
same bit used by CASP — and callers can override ``flag_word``/``bit``
via :func:`patch_cobj_flags`.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field


class CobjError(Exception):
    pass


@dataclass
class CobjObject:
    version: int
    name_hash: int
    description_hash: int
    price: int
    tag_count: int
    selling_point_count: int
    buildbuy: int
    flag_offsets: dict[str, int] = field(default_factory=dict)
    flag_values: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "version": f"0x{self.version:X}",
            "name_hash": f"0x{self.name_hash:08X}",
            "description_hash": f"0x{self.description_hash:08X}",
            "price": self.price,
            "tags": self.tag_count,
            "selling_points": self.selling_point_count,
            "buildbuy": "buy" if self.buildbuy == 1 else "build",
            "flags": {k: f"0x{v:08X}"
                      for k, v in self.flag_values.items()},
        }


def parse_cobj(data: bytes) -> CobjObject:
    r = io.BytesIO(data)
    try:
        version = struct.unpack("<I", r.read(4))[0]
        common_version = struct.unpack("<I", r.read(4))[0]
        name_hash, desc_hash, price = struct.unpack("<III", r.read(12))
        r.read(12)  # unkCommon01..03
        style_count = struct.unpack("<B", r.read(1))[0]
        r.read(16 * style_count)  # QWORD iid + DWORD tid + DWORD gid
        r.read(2)  # unkCommon04
        tag_count = struct.unpack("<I", r.read(4))[0]
        r.read(2 * tag_count)
        sp_count = struct.unpack("<I", r.read(4))[0]
        r.read(6 * sp_count)  # WORD commodity + DWORD amount
        r.read(8)  # unkCommon05
        r.read(2)  # unkCommon06
        r.read(8)  # unkCommon07

        # trailing catalog block
        r.read(4 * 7)  # unk01, hash01..03, unk02, hash04, hash05
        flag_offsets: dict[str, int] = {}
        flag_values: dict[str, int] = {}
        for label in ("unkFlags01", "categoryFlags", "unkFlags03",
                      "unkFlags04", "placementFlags"):
            flag_offsets[label] = r.tell()
            flag_values[label] = struct.unpack("<I", r.read(4))[0]
        r.read(8)   # unkIID01
        r.read(1)   # unk03
        r.read(8)   # unkIID02
        r.read(1)   # unk04
        color_count = struct.unpack("<B", r.read(1))[0]
        r.read(4 * color_count)
        r.read(5)   # unk05..unk09
        buildbuy = struct.unpack("<B", r.read(1))[0]
    except struct.error as e:
        raise CobjError(f"truncated COBJ: {e}") from e

    return CobjObject(
        version=version, name_hash=name_hash, description_hash=desc_hash,
        price=price, tag_count=tag_count, selling_point_count=sp_count,
        buildbuy=buildbuy, flag_offsets=flag_offsets,
        flag_values=flag_values)


def patch_cobj_flags(data: bytearray, *,
                     allow_for_random: bool | None = None,
                     show_in_ui: bool | None = None,
                     flag_word: str = "categoryFlags",
                     bit_allow_for_random: int = 2,
                     bit_show_in_ui: int = 3,
                     ) -> tuple[bytearray, dict]:
    """Patch a COBJ flag word in place.

    Defaults follow the CASP bit convention (bit 2 = AllowForRandom,
    bit 3 = ShowInUI).  Pass ``flag_word``/``bit_*`` to target a
    different documented-by-discovery word/bit.
    """
    obj = parse_cobj(bytes(data))
    if flag_word not in obj.flag_offsets:
        raise CobjError(f"unknown flag word {flag_word!r}; choose from "
                        f"{sorted(obj.flag_offsets)}")
    offset = obj.flag_offsets[flag_word]
    value = obj.flag_values[flag_word]
    changes: dict[str, dict] = {}
    for bit, wanted, name in ((bit_allow_for_random, allow_for_random,
                               "AllowForRandom"),
                              (bit_show_in_ui, show_in_ui, "ShowInUI")):
        if wanted is None:
            continue
        before = bool(value & (1 << bit))
        if wanted:
            value |= (1 << bit)
        else:
            value &= ~(1 << bit)
        changes[name] = {"before": before, "after": wanted}
    struct.pack_into("<I", data, offset, value & 0xFFFFFFFF)
    return data, {"version": f"0x{obj.version:X}",
                  "name_hash": f"0x{obj.name_hash:08X}",
                  "word": flag_word, "changes": changes}
