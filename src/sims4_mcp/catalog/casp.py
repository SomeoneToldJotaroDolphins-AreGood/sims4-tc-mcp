"""CASP (CAS Part, ``0x034AEECB``) binary parser and flag patcher.

Layout implemented from the official Maxis documentation released by
SimGuruModSquad (2015-06-13), current version ``0x20``::

    UINT32 version
    UINT32 dataSize                  (bytes before the key table)
    -- data --
    UINT32 numPresets
    STRING name                      (u32 char count + UTF-16BE)
    FLOAT  displayIndex
    UINT16 secondaryDisplayIndex
    UINT32 outfitId
    UINT32 auralMaterialHash
    BYTE   partFlags                 <- AllowForRandom = bit 2 (0x04)
    UINT64 excludePartFlags
    UINT32 excludeModifierRegionFlags
    UINT32 tagCount; Tag[tagCount]   (u16 category, u16 value)
    UINT32 simoleonPrice
    UINT32 partTitleKey
    UINT32 partDescKey
    BYTE   uniqueTextureSpace
    INT32  bodyType
    INT32  unused1
    UINT32 ageGender
    ... version-gated fields ...
    BYTE keyCount; Key[keyCount]     (u64 instance, u32 group, u32 type)

Patching is strictly in-place: every byte other than the targeted flag
is preserved, so override packages stay byte-identical elsewhere.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..dbpf.package import PackageEntry
from ..models import TGI, TYPE_CASP

#: partFlags bit names (bit -> name)
CASP_FLAGS: dict[int, str] = {
    0: "DefaultForBodyType",
    1: "DefaultThumbnailPart",
    2: "AllowForRandom",
    3: "ShowInUI",
    4: "ShowInSimInfoPanel",
    5: "ShowInCasDemo",
}


class CaspError(Exception):
    pass


@dataclass
class CaspPart:
    version: int
    name: str
    display_index: float
    part_flags: int
    age_gender: int
    body_type: int
    simoleon_price: int
    keys: list[TGI] = field(default_factory=list)
    part_flags_offset: int = 0
    age_gender_offset: int = 0

    def flags(self) -> dict[str, bool]:
        return {name: bool(self.part_flags & (1 << bit))
                for bit, name in CASP_FLAGS.items()}

    def as_dict(self) -> dict:
        return {
            "version": f"0x{self.version:X}",
            "name": self.name,
            "display_index": self.display_index,
            "body_type": self.body_type,
            "age_gender": f"0x{self.age_gender:08X}",
            "simoleon_price": self.simoleon_price,
            "part_flags": f"0x{self.part_flags:02X}",
            "flags": self.flags(),
            "keys": [str(k) for k in self.keys],
        }


def _read_name(r, version: int) -> str:
    """Read the CASP name, advancing the stream.

    Maxis documents this as a UnicodeBE string with a uint32 char count,
    but some tooling emits ASCII/UTF-8.  Probe both and keep the one that
    decodes to printable text.
    """
    start = r.tell()
    length = struct.unpack("<I", r.read(4))[0]
    if length > 4096:
        raise CaspError(f"implausible CASP name length {length}")

    candidates: list[tuple[str, int, str]] = []
    for enc, nbytes in (("utf-16-be", length * 2), ("utf-8", length),
                        ("utf-16-le", length * 2)):
        blob = _peek(r, start + 4, nbytes)
        if len(blob) < nbytes:
            continue
        try:
            text = blob.decode(enc)
        except UnicodeDecodeError:
            continue
        if text and all(c.isprintable() for c in text):
            candidates.append((enc, nbytes, text))
    if not candidates:
        raise CaspError("could not decode CASP name")
    order = {"utf-16-be": 0, "utf-8": 1, "utf-16-le": 2}
    candidates.sort(key=lambda c: order[c[0]])
    _enc, nbytes, text = candidates[0]
    r.seek(start + 4 + nbytes)
    return text


def _peek(r, pos: int, n: int) -> bytes:
    cur = r.tell()
    r.seek(pos)
    data = r.read(n)
    r.seek(cur)
    return data


def parse_casp(data: bytes) -> CaspPart:
    import io
    r = io.BytesIO(data)
    version, data_size = struct.unpack("<II", r.read(8))
    if version < 0x18 or version > 0x2A:
        raise CaspError(f"unsupported CASP version 0x{version:X}")
    struct.unpack("<I", r.read(4))[0]  # numPresets (unused)
    name = _read_name(r, version)
    display_index = struct.unpack("<f", r.read(4))[0]
    r.read(2)  # secondary display index
    r.read(4)  # outfit id
    r.read(4)  # aural material hash

    flags_offset = r.tell()
    part_flags = struct.unpack("<B", r.read(1))[0]

    # remaining fields are parsed only far enough to be informative
    try:
        r.read(8)   # excludePartFlags
        r.read(4)   # excludeModifierRegionFlags
        tag_count = struct.unpack("<I", r.read(4))[0]
        r.read(4 * tag_count)
        simoleon_price = struct.unpack("<I", r.read(4))[0]
        r.read(4)  # partTitleKey
        r.read(4)  # partDescKey
        r.read(1)  # uniqueTextureSpace
        body_type = struct.unpack("<i", r.read(4))[0]
        r.read(4)  # unused1
        age_gender_offset = r.tell()
        age_gender = struct.unpack("<I", r.read(4))[0]
    except struct.error:
        # truncated/experimental versions: still expose what we parsed
        simoleon_price, body_type = 0, 0
        age_gender, age_gender_offset = 0, 0

    keys: list[TGI] = []
    # key table is at the end of the data block
    try:
        end = min(len(data), 8 + data_size)
        if end >= 9:
            count = data[end - 1]
            pos = end - 1 - count * 16
            if pos >= 0:
                for i in range(count):
                    inst, group, rtype = struct.unpack(
                        "<QII", data[pos + i * 16: pos + i * 16 + 16])
                    keys.append(TGI(type=rtype, group=group, instance=inst))
    except (struct.error, IndexError):
        keys = []

    return CaspPart(
        version=version, name=name, display_index=display_index,
        part_flags=part_flags, age_gender=age_gender, body_type=body_type,
        simoleon_price=simoleon_price, keys=keys,
        part_flags_offset=flags_offset,
        age_gender_offset=age_gender_offset)


def patch_casp_flags(data: bytearray, allow_for_random: bool | None = None,
                     show_in_ui: bool | None = None,
                     show_in_sim_info_panel: bool | None = None,
                     show_in_cas_demo: bool | None = None,
                     ) -> tuple[bytearray, dict]:
    """Patch CASP partFlags bits in place.  ``None`` leaves a bit alone."""
    part = parse_casp(bytes(data))
    flags = part.part_flags
    changes: dict[str, bool] = {}
    for bit, value in (
        (2, allow_for_random),
        (3, show_in_ui),
        (4, show_in_sim_info_panel),
        (5, show_in_cas_demo),
    ):
        if value is None:
            continue
        name = CASP_FLAGS[bit]
        before = bool(flags & (1 << bit))
        if value:
            flags |= (1 << bit)
        else:
            flags &= ~(1 << bit)
        changes[name] = {"before": before, "after": value}
    data[part.part_flags_offset] = flags & 0xFF
    return data, {"name": part.name, "version": f"0x{part.version:X}",
                  "changes": changes}


def casp_entry_from_bytes(tgi: TGI, data: bytes, name: str | None = None,
                          ) -> PackageEntry:
    return PackageEntry(tgi=tgi, data=data, name=name)


def make_casp_tgi(instance: int, group: int = 0x1C000000) -> TGI:
    return TGI(type=TYPE_CASP, group=group, instance=instance)
