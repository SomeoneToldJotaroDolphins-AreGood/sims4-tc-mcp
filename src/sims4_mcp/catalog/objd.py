"""OBJD (Object Definition, ``0xC0DB5AE7``) TLV parser.

OBJD is a tagged entry list::

    WORD  unk1
    DWORD entryRefListPosition
    WORD  entryRefCount
    per entry: DWORD entryType | DWORD position
    ... entry payloads ...

Known entry types (payload kinds in comments) are parsed where their
type is documented; unknown entries are preserved verbatim, making
in-place edits safe.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

ENTRY_TYPES: dict[int, str] = {
    0xE7F07786: "Name (STRING1)",
    0x790FA4BC: "ObjectTuningName (STRING2)",
    0xB994039B: "ObjectTuningIID (QWORD)",
    0xCADED888: "IconRef",
    0xE206AE4F: "RigRef",
    0x8A85AFF3: "RSLTRef",
    0x8D20ACC6: "MODLRef",
    0x6C737AD8: "FTPTRef",
    0xE6E421FB: "Components",
    0xECD5A95F: "MaterialVariantName (STRING3)",
    0xE4F4FAA4: "Price (FLOAT)",
    0xDCD08394: "EnvironmentScores (FLOAT)",
    0x2172AEBE: "EnvironmentScoreEmotionTags (SHORT)",
    0x7236BEEA: "PositiveEnvironmentScore (FLOAT)",
    0x44FC7512: "NegativeEnvironmentScore (FLOAT)",
    0x4233F8A0: "ThumbnailGeometryState (UINT32)",
    0xAEE67A1C: "IsBaby (BOOLEAN)",
    0xAC8E1BC0: "Unknown1 (BYTE)",
    0xEC3712E6: "Unknown2 (BOOLEAN)",
    0x52F7F4BC: "Unknown3 (ULONG)",
    0xF3936A90: "Unknown4 (BYTE ARRAY)",
}


@dataclass
class ObjdEntry:
    entry_type: int
    position: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return ENTRY_TYPES.get(self.entry_type,
                               f"Unknown(0x{self.entry_type:08X})")


@dataclass
class ObjdObject:
    unk1: int
    entries: list[ObjdEntry] = field(default_factory=list)

    def get(self, entry_type: int) -> ObjdEntry | None:
        for e in self.entries:
            if e.entry_type == entry_type:
                return e
        return None

    def as_dict(self) -> dict:
        return {
            "unk1": self.unk1,
            "entries": [{"type": f"0x{e.entry_type:08X}",
                         "name": e.type_name,
                         "position": e.position,
                         "size": len(e.payload)} for e in self.entries],
        }


def parse_objd(data: bytes) -> ObjdObject:
    try:
        unk1, list_pos, count = struct.unpack_from("<HIH", data, 0)
    except struct.error as e:
        raise ValueError(f"truncated OBJD header: {e}") from e
    entries = []
    pos = list_pos
    for _ in range(count):
        etype, epos = struct.unpack_from("<II", data, pos)
        # payload extends to the next entry position (or EOF)
        entries.append(ObjdEntry(entry_type=etype, position=epos,
                                 payload=b""))
        pos += 8
    positions = sorted({e.position for e in entries} | {len(data)})
    for e in entries:
        idx = positions.index(e.position)
        end = positions[idx + 1] if idx + 1 < len(positions) else len(data)
        e.payload = data[e.position:end]
    return ObjdObject(unk1=unk1, entries=entries)
