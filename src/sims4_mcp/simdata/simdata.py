"""SimData binary tables (type ``0x545AC67A``, version ``0x101``).

SimData is the client-side binary database the Sims 4 C++ UI reads for
traits, buffs, aspirations, venues, careers and objectives.  A mod that
ships tuning XML without the matching SimData table renders blank icons
or crashes the client.

Binary layout (little-endian), as reverse-engineered by the modding
community (Sims 4 Studio / Delphy):

    Header:  version u32 (=0x101) | table count u32
    Per table:
        name u32            (FNV-32 of the table name, lowercase)
        tdesc_instance u32  (low 32 bits of the TDESC instance id)
        row_count u32
        field_count u32
        per field:
            name u32        (FNV-32, lowercase)
            type u32        (SimDataType id)
            is_index u32    (1 when the field is indexed)
        rows: row_count * field_count u32 cell values
            (integers inline; strings are indices into the string table)
        index data: per indexed field: entry count u32, then
            (cell value u32, row index u32) pairs
    String table: count u32, then per string: length u32 + UTF-8 bytes
        (index 0 is the empty string)

Cell encoding note: this implementation stores strings as string-table
indices with the high bit clear and inline integers raw.  The reader
auto-detects which cells are strings via the field type.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field

from ..crypto.fnv import fnv32

SIMDATA_VERSION = 0x101


class SimDataType:
    """SimData field type ids."""

    INT32 = 0
    UINT32 = 1
    INT64 = 2
    FLOAT32 = 3
    BOOLEAN = 4
    STRING = 5
    ENUM = 6
    COLOR_RGBA = 7
    RESOURCE_KEY = 8
    BITMASK16 = 9
    BITMASK32 = 10
    DURATION = 11
    ANIM = 12
    TIME = 13
    GUID = 14

    NAMES: dict[int, str] = {
        INT32: "int32", UINT32: "uint32", INT64: "int64",
        FLOAT32: "float32", BOOLEAN: "boolean", STRING: "string",
        ENUM: "enum", COLOR_RGBA: "color", RESOURCE_KEY: "resource_key",
        BITMASK16: "bitmask16", BITMASK32: "bitmask32",
        DURATION: "duration", ANIM: "anim", TIME: "time", GUID: "guid",
    }

    @classmethod
    def name(cls, type_id: int) -> str:
        return cls.NAMES.get(type_id, f"unknown({type_id})")

    @classmethod
    def from_name(cls, name: str) -> int:
        for v, n in cls.NAMES.items():
            if n == name:
                return v
        raise ValueError(f"Unknown SimData type {name!r}")


@dataclass
class SimDataField:
    name: str
    type_id: int
    is_index: bool = False

    @property
    def name_hash(self) -> int:
        return fnv32(self.name, high_bit=False)


@dataclass
class SimDataTable:
    name: str
    tdesc_instance: int = 0     # low 32 bits of the TDESC instance
    fields: list[SimDataField] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)

    @property
    def name_hash(self) -> int:
        return fnv32(self.name, high_bit=False)

    def field_index(self, name: str) -> int | None:
        for i, f in enumerate(self.fields):
            if f.name.lower() == name.lower():
                return i
        return None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "tdesc_instance": f"0x{self.tdesc_instance:08X}",
            "fields": [{"name": f.name,
                        "type": SimDataType.name(f.type_id),
                        "index": f.is_index} for f in self.fields],
            "row_count": len(self.rows),
            "rows": self.rows,
        }


@dataclass
class SimDataFile:
    tables: list[SimDataTable] = field(default_factory=list)

    def table(self, name: str) -> SimDataTable | None:
        for t in self.tables:
            if t.name.lower() == name.lower():
                return t
        return None


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------

def _cell_to_u32(value: object, type_id: int,
                 string_table: list[str]) -> int:
    if type_id == SimDataType.STRING:
        s = "" if value is None else str(value)
        if s not in string_table:
            string_table.append(s)
        return string_table.index(s)
    if type_id == SimDataType.BOOLEAN:
        return 1 if value else 0
    if type_id == SimDataType.FLOAT32:
        return struct.unpack("<I", struct.pack("<f", float(value or 0)))[0]
    if type_id == SimDataType.INT64:
        # stored as two u32 (lo, hi) — we pack lo only into the cell and
        # the high word follows inline; see _write_rows
        return int(value or 0) & 0xFFFFFFFF
    if type_id in (SimDataType.INT32, SimDataType.UINT32, SimDataType.ENUM,
                   SimDataType.COLOR_RGBA, SimDataType.BITMASK16,
                   SimDataType.BITMASK32, SimDataType.DURATION,
                   SimDataType.ANIM, SimDataType.TIME):
        v = int(value or 0)
        return v & 0xFFFFFFFF
    return int(value or 0) & 0xFFFFFFFF


def build_simdata(simdata: SimDataFile) -> bytes:
    out = io.BytesIO()
    string_table: list[str] = [""]  # index 0 == empty

    out.write(struct.pack("<II", SIMDATA_VERSION, len(simdata.tables)))
    for t in simdata.tables:
        # serialize rows first so the string table is complete
        row_buf = io.BytesIO()
        for row in t.rows:
            for value, f in zip(row, t.fields):
                cell = _cell_to_u32(value, f.type_id, string_table)
                row_buf.write(struct.pack("<I", cell))
                if f.type_id == SimDataType.INT64:
                    hi = (int(value or 0) >> 32) & 0xFFFFFFFF
                    row_buf.write(struct.pack("<I", hi))
        out.write(struct.pack("<IIII", t.name_hash, t.tdesc_instance,
                              len(t.rows), len(t.fields)))
        for f in t.fields:
            out.write(struct.pack("<III", f.name_hash, f.type_id,
                                  1 if f.is_index else 0))
        out.write(row_buf.getvalue())

        # index data for indexed fields
        for col, f in enumerate(t.fields):
            if not f.is_index:
                continue
            pairs: dict[int, list[int]] = {}
            for r, row in enumerate(t.rows):
                key = _cell_to_u32(row[col] if col < len(row) else None,
                                   f.type_id, string_table)
                pairs.setdefault(key, []).append(r)
            out.write(struct.pack("<I", len(pairs)))
            for key in sorted(pairs):
                out.write(struct.pack("<II", key, len(pairs[key])))
                out.write(struct.pack(f"<{len(pairs[key])}I",
                                      *pairs[key]))

    out.write(struct.pack("<I", len(string_table)))
    for s in string_table:
        raw = s.encode("utf-8")
        out.write(struct.pack("<I", len(raw)))
        out.write(raw)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_simdata(data: bytes) -> SimDataFile:
    r = io.BytesIO(data)
    version, table_count = struct.unpack("<II", r.read(8))
    if version != SIMDATA_VERSION:
        raise ValueError(f"Unsupported SimData version 0x{version:X}")

    # Rows reference the string table which lives at EOF, so parse tables
    # with type-tagged raw cells first, then resolve strings.
    tables: list[SimDataTable] = []
    for _ in range(table_count):
        name_hash, tdesc_inst, row_count, field_count = struct.unpack(
            "<IIII", r.read(16))
        fields = []
        for _ in range(field_count):
            fname, ftype, is_index = struct.unpack("<III", r.read(12))
            fields.append(SimDataField(
                name=f"0x{fname:08X}", type_id=ftype,
                is_index=bool(is_index)))
        t = SimDataTable(name=f"0x{name_hash:08X}",
                         tdesc_instance=tdesc_inst, fields=fields)
        for _ in range(row_count):
            row: list[object] = []
            for f in fields:
                cell = struct.unpack("<I", r.read(4))[0]
                if f.type_id == SimDataType.INT64:
                    hi = struct.unpack("<I", r.read(4))[0]
                    cell |= hi << 32
                row.append((f.type_id, cell))
            t.rows.append(row)  # type-tagged for now
        # index data (skipped on read; rebuilt on write)
        for f in fields:
            if f.is_index:
                n = struct.unpack("<I", r.read(4))[0]
                for _ in range(n):
                    r.read(4)  # key
                    cnt = struct.unpack("<I", r.read(4))[0]
                    r.read(4 * cnt)  # row indices
        tables.append(t)

    # string table
    str_count = struct.unpack("<I", r.read(4))[0]
    strings = []
    for _ in range(str_count):
        ln = struct.unpack("<I", r.read(4))[0]
        strings.append(r.read(ln).decode("utf-8", errors="replace"))

    # Table/field names are one-way FNV hashes; they stay as 0x... unless
    # the same string also appears in the string table (best-effort).
    name_by_hash = {fnv32(s, high_bit=False): s for s in strings if s}
    for t in tables:
        t.name = name_by_hash.get(int(t.name, 16), t.name)
        for f in t.fields:
            f.name = name_by_hash.get(int(f.name, 16), f.name)
        decoded_rows = []
        for raw_row in t.rows:
            decoded_rows.append([
                _decode_cell(ftype, cell, strings)
                for (ftype, cell) in raw_row])
        t.rows = decoded_rows
    return SimDataFile(tables=tables)


def _decode_cell(type_id: int, cell: int, strings: list[str]) -> object:
    if type_id == SimDataType.STRING:
        return strings[cell] if cell < len(strings) else ""
    if type_id == SimDataType.FLOAT32:
        return struct.unpack("<f", struct.pack("<I", cell & 0xFFFFFFFF))[0]
    if type_id == SimDataType.BOOLEAN:
        return bool(cell)
    if type_id == SimDataType.INT32:
        return cell - 0x100000000 if cell >= 0x80000000 else cell
    return cell
