"""DBPF 2.1 ``.package`` binary reader/writer for The Sims 4.

Layout (index version 3, as used by every modern Sims 4 package)::

    Package 0 header:  magic 'DBPF', major=2, minor=1,
                       index version=3, [64] reserved,
                       type count, dir count, file count
    Index:             per-entry records + optional adjustment tables
    Data entries:      [compressed size u32][file size u32][payload]
                       payload starts with magic 0x5A42 + u32 size + zlib
                       stream when compressed, else raw bytes
    Name table:        'NAME' magic, entries, UTF-16 names
    Index Data Offset: uint32 at end of file pointing at the index

Compression signal: an entry is compressed iff its stored *compressed
size* differs from its *file size* (the game relies on this, not the
magic alone).  We still validate the 0x5A42 magic when present.
"""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass, field

from ..models import TGI

MAGIC_DBPF = b"DBPF"
MAGIC_NAME = b"NAME"
MAGIC_ZLIB = 0x5A42
MAGIC_NONE = 0x0000

# Index v3 entry flags
FLAG_CONSTANT_TYPE = 0x1
FLAG_CONSTANT_GROUP = 0x2
FLAG_CONSTANT_INSTANCE = 0x4
FLAG_INSTANCE_ADJUSTMENTS = 0x8
FLAG_TYPE_ADJUSTMENTS = 0x10
FLAG_GROUP_ADJUSTMENTS = 0x20
FLAG_HAS_NAME_KEYS = 0x40

_ZLIB_THRESHOLD = 0x100  # don't bother compressing tiny payloads


@dataclass
class PackageEntry:
    """One resource inside a package."""

    tgi: TGI
    data: bytes = b""
    name: str | None = None
    # raw offsets preserved from the source package (writer recomputes)
    data_offset: int = 0
    compressed_size: int = 0
    file_size: int = 0

    @property
    def instance_hi(self) -> int:
        return (self.tgi.instance >> 32) & 0xFFFFFFFF

    @property
    def instance_lo(self) -> int:
        return self.tgi.instance & 0xFFFFFFFF


@dataclass
class Package:
    """A parsed DBPF 2.1 package."""

    entries: list[PackageEntry] = field(default_factory=list)
    index_version: int = 3

    def find(self, tgi: TGI) -> PackageEntry | None:
        for e in self.entries:
            if e.tgi == tgi:
                return e
        return None


class PackageError(Exception):
    """Raised for malformed package data."""


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def read_package(path: str) -> Package:
    with open(path, "rb") as fh:
        return parse_package(fh.read())


def parse_package(blob: bytes) -> Package:
    r = io.BytesIO(blob)
    magic = r.read(4)
    if magic != MAGIC_DBPF:
        raise PackageError(f"Bad magic {magic!r}, expected b'DBPF'")
    major, minor = struct.unpack("<HH", r.read(4))
    if (major, minor) != (2, 1):
        raise PackageError(f"Unsupported DBPF version {major}.{minor}")
    index_version = struct.unpack("<I", r.read(4))[0]
    if index_version > 3:
        raise PackageError(f"Unsupported index version {index_version}")
    r.read(64)  # reserved
    type_count, dir_count, file_count = struct.unpack("<III", r.read(12))

    # Index data offset lives at EOF (v1+).
    index_data_offset = struct.unpack("<I", blob[-4:])[0]

    # Parse the index first; the name table (v3) starts right after it.
    r.seek(index_data_offset)
    entries, flags, index_end = _read_index(
        r, index_version, file_count, type_count, dir_count)

    names: list[str] = []
    if index_version >= 3 and (flags & FLAG_HAS_NAME_KEYS):
        names = _read_name_table(blob, index_end)

    resolved = []
    for tgi, name_key, data_off, comp_size, file_size in entries:
        name = None
        if 0 < name_key <= len(names):
            name = names[name_key - 1]
        resolved.append((tgi, name, data_off, comp_size, file_size))

    pkg = Package(index_version=index_version)
    for tgi, name, data_off, comp_size, file_size in resolved:
        r.seek(data_off)
        stored = r.read(comp_size)
        data = _decode_payload(stored, comp_size, file_size)
        pkg.entries.append(PackageEntry(
            tgi=tgi, data=data, name=name,
            data_offset=data_off,
            compressed_size=comp_size, file_size=file_size))
    return pkg


def _read_name_table(blob: bytes, pos: int) -> list[str]:
    """Parse the NAME table starting exactly at ``pos``."""
    if blob[pos:pos + 4] != MAGIC_NAME:
        return []
    r = io.BytesIO(blob)
    r.seek(pos)
    r.read(4)  # magic
    entry_count, name_size = struct.unpack("<II", r.read(8))
    r.read(20)  # unused
    offsets = struct.unpack(f"<{entry_count}I", r.read(4 * entry_count))
    raw = r.read(name_size)
    names = []
    for off in offsets:
        chunk = raw[off:]
        stop = chunk.find(b"\x00\x00")
        while stop != -1 and stop % 2:
            stop = chunk.find(b"\x00\x00", stop + 1)
        if stop == -1:
            stop = len(chunk)
        names.append(chunk[:stop].decode("utf-16-le", errors="replace"))
    return names


def _read_index(r: io.BytesIO, index_version: int, file_count: int,
                type_count: int, dir_count: int,
                ) -> tuple[list[tuple[TGI, int, int, int, int]], int, int]:
    """Parse the index; returns (entries with raw name keys, flags, end pos).

    The caller has already seeked to the index data offset.
    """
    flags = 0
    if index_version >= 3:
        flags = struct.unpack("<I", r.read(4))[0]

    constant_type = constant_group = constant_instance = 0
    if flags & FLAG_CONSTANT_TYPE:
        constant_type = struct.unpack("<I", r.read(4))[0]
    if flags & FLAG_CONSTANT_GROUP:
        constant_group = struct.unpack("<I", r.read(4))[0]
    if index_version >= 2 and (flags & FLAG_CONSTANT_INSTANCE):
        constant_instance = struct.unpack("<I", r.read(4))[0]

    # Adjustment tables
    type_adj: dict[int, int] = {}
    group_adj: dict[int, int] = {}
    inst_adj: dict[int, int] = {}
    if flags & FLAG_TYPE_ADJUSTMENTS:
        count = struct.unpack("<I", r.read(4))[0]
        for _ in range(count):
            k, v = struct.unpack("<II", r.read(8))
            type_adj[k] = v
    if flags & FLAG_GROUP_ADJUSTMENTS:
        count = struct.unpack("<I", r.read(4))[0]
        for _ in range(count):
            k, v = struct.unpack("<II", r.read(8))
            group_adj[k] = v
    if index_version >= 2 and (flags & FLAG_INSTANCE_ADJUSTMENTS):
        count = struct.unpack("<I", r.read(4))[0]
        for _ in range(count):
            k, v = struct.unpack("<II", r.read(8))
            inst_adj[k] = v

    entries: list[tuple[TGI, int, int, int, int]] = []
    for i in range(file_count):
        t = struct.unpack("<I", r.read(4))[0]
        if t == 0:
            t = constant_type
        elif t in type_adj:
            t = type_adj[t]
        g = struct.unpack("<I", r.read(4))[0]
        if g == 0:
            g = constant_group
        elif g in group_adj:
            g = group_adj[g]
        inst_lo = struct.unpack("<I", r.read(4))[0]
        if index_version >= 2:
            if inst_lo == 0 and (flags & FLAG_CONSTANT_INSTANCE):
                inst_lo = constant_instance
            elif inst_lo in inst_adj:
                inst_lo = inst_adj[inst_lo]
            inst_hi = struct.unpack("<I", r.read(4))[0]
        else:
            inst_hi = 0
        instance = (inst_hi << 32) | inst_lo
        data_off, comp_size, file_size = struct.unpack("<III", r.read(12))
        if index_version >= 1:
            r.read(4)  # instance number (unused)
        name_key = 0
        if index_version >= 3 and (flags & FLAG_HAS_NAME_KEYS):
            name_key = struct.unpack("<I", r.read(4))[0]
        entries.append((TGI(type=t, group=g, instance=instance),
                        name_key, data_off, comp_size, file_size))
    return entries, flags, r.tell()

def _decode_payload(stored: bytes, comp_size: int, file_size: int) -> bytes:
    if comp_size == file_size:
        return stored  # uncompressed
    magic, size = struct.unpack("<II", stored[:8])
    if magic == MAGIC_ZLIB:
        data = zlib.decompress(stored[8:comp_size])
        if len(data) != file_size:
            raise PackageError(
                f"Payload size mismatch: {len(data)} != {file_size}")
        return data
    # No BZ magic: raw deflate stream
    data = zlib.decompress(stored[8:comp_size])
    if len(data) != file_size:
        raise PackageError(f"Payload size mismatch: {len(data)} != {file_size}")
    return data


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _encode_payload(data: bytes) -> tuple[bytes, int]:
    """Return (stored_bytes, file_size). Compresses when it helps."""
    if len(data) >= _ZLIB_THRESHOLD:
        compressed = zlib.compress(data, 6)
        framed = struct.pack("<II", MAGIC_ZLIB, len(data)) + compressed
        if len(framed) < len(data):
            return framed, len(data)
    return data, len(data)


def build_package(entries: list[PackageEntry]) -> bytes:
    """Serialize entries into a DBPF 2.1 package (index v3)."""
    entries = sorted(entries, key=lambda e: (e.tgi.type, e.tgi.group,
                                             e.tgi.instance))
    if not entries:
        raise PackageError("Cannot build an empty package")

    types = {e.tgi.type for e in entries}
    groups = {e.tgi.group for e in entries}
    inst_lo = {e.instance_lo for e in entries}
    inst_hi = {e.instance_hi for e in entries}

    flags = 0
    constant_type = constant_group = constant_instance = 0
    type_adj: dict[int, int] = {}
    group_adj: dict[int, int] = {}
    inst_adj: dict[int, int] = {}

    n_types, n_groups = len(types), len(groups)

    # Constant encoding is only worth it when the majority share a value;
    # the sentinel 0 triggers the constant path per entry.
    if len(types) == 1:
        flags |= FLAG_CONSTANT_TYPE
        constant_type = types.pop()
    if len(groups) == 1:
        flags |= FLAG_CONSTANT_GROUP
        constant_group = groups.pop()
    if len(inst_lo) == 1 and len(inst_hi) == 1:
        flags |= FLAG_CONSTANT_INSTANCE
        constant_instance = inst_lo.pop()

    has_names = any(e.name for e in entries)
    if has_names:
        flags |= FLAG_HAS_NAME_KEYS

    out = io.BytesIO()
    # ---- header (index offset patched later) ----
    out.write(MAGIC_DBPF)
    out.write(struct.pack("<HHI", 2, 1, 3))
    out.write(b"\x00" * 64)
    out.write(struct.pack("<III", n_types, n_groups, len(entries)))

    # ---- index ----
    index_start = out.tell()
    out.write(struct.pack("<I", flags))
    if flags & FLAG_CONSTANT_TYPE:
        out.write(struct.pack("<I", constant_type))
    if flags & FLAG_CONSTANT_GROUP:
        out.write(struct.pack("<I", constant_group))
    if flags & FLAG_CONSTANT_INSTANCE:
        out.write(struct.pack("<I", constant_instance))
    for table, flag in ((type_adj, FLAG_TYPE_ADJUSTMENTS),
                        (group_adj, FLAG_GROUP_ADJUSTMENTS),
                        (inst_adj, FLAG_INSTANCE_ADJUSTMENTS)):
        if flags & flag:
            out.write(struct.pack("<I", len(table)))
            for k, v in sorted(table.items()):
                out.write(struct.pack("<II", k, v))

    name_keys: list[str] = []
    name_index: dict[str, int] = {}
    if has_names:
        for e in entries:
            if e.name and e.name not in name_index:
                name_index[e.name] = len(name_keys) + 1  # 1-based, 0 = none
                name_keys.append(e.name)

    # data section placeholder: we must know data offsets before writing
    # the index, so compute sizes first.
    encoded = [_encode_payload(e.data) for e in entries]
    # record: type(4) group(4) inst_lo(4) inst_hi(4) data_off(4) comp(4)
    #         file(4) instance_num(4) [name_key(4)]
    record_size = 32 + (4 if has_names else 0)
    index_size = out.tell() - index_start + len(entries) * record_size
    name_table_size = _name_table_size(name_keys)
    data_start = index_start + index_size + name_table_size

    cursor = data_start
    offsets = []
    for stored, file_size in encoded:
        offsets.append((cursor, len(stored), file_size))
        cursor += len(stored)

    for e, (data_off, comp_size, file_size) in zip(entries, offsets):
        t = 0 if (flags & FLAG_CONSTANT_TYPE and
                  e.tgi.type == constant_type) else e.tgi.type
        g = 0 if (flags & FLAG_CONSTANT_GROUP and
                  e.tgi.group == constant_group) else e.tgi.group
        il = (0 if (flags & FLAG_CONSTANT_INSTANCE and
                    e.instance_lo == constant_instance) else e.instance_lo)
        out.write(struct.pack("<IIIIIII", t, g, il, e.instance_hi,
                              data_off, comp_size, file_size))
        out.write(struct.pack("<I", 0))  # instance number
        if has_names:
            key = name_index.get(e.name or "", 0)
            out.write(struct.pack("<I", key))

    if has_names:
        _write_name_table(out, name_keys)

    for e, (stored, _file_size) in zip(entries, encoded):
        out.write(stored)

    out.write(struct.pack("<I", index_start))
    return out.getvalue()


def _name_table_size(names: list[str]) -> int:
    if not names:
        return 0
    raw_bytes = sum(len(n.encode("utf-16-le")) + 2 for n in names)
    return 32 + 4 * len(names) + raw_bytes


def _write_name_table(out: io.BytesIO, names: list[str]) -> None:
    out.write(MAGIC_NAME)
    raw = b""
    offsets = []
    for n in names:
        offsets.append(len(raw))
        raw += n.encode("utf-16-le") + b"\x00\x00"
    out.write(struct.pack("<II", len(names), len(raw)))
    out.write(b"\x00" * 20)
    for off in offsets:
        out.write(struct.pack("<I", off))
    out.write(raw)


def write_package(pkg: Package, path: str) -> None:
    blob = build_package(pkg.entries)
    with open(path, "wb") as fh:
        fh.write(blob)
