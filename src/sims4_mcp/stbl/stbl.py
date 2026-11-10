"""Binary String Table (STBL) v5 parser/builder for The Sims 4.

Resource: type ``0x220557DA``, group ``0xFFFFFFFF``.
Instance: ``fnv32(base_name)`` with the locale code in the high byte
(``0x00`` en_US, ``0x01`` zh_CN, ...).

Binary layout (little-endian)::

    Header:  'STBL' | version u32 (=5) | format u16 (=1) |
             language u32 | entry count u32 | string buffer size u32 |
             unused u64
    Entries: key u32 | flags u8 | sid u16 | line u16 |
             string length u16 | string offset u32
    Meta:    entry count u32 | per-entry: offset u32 | length u32
    Strings: null-terminated UTF-8 blob

Keys are high-bit FNV-32 hashes of the *file name* the string is
referenced from (e.g. ``0x80000000 | fnv32("modname:object.description")``).
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field

from ..crypto.fnv import fnv32

STBL_MAGIC = b"STBL"
STBL_VERSION = 5
STBL_FORMAT = 1

#: Sims 4 locale code -> (instance high byte, language id in header)
LOCALES: dict[str, tuple[int, int]] = {
    "en_us": (0x00, 1),
    "de_de": (0x01, 2),
    "fr_fr": (0x02, 3),
    "en_gb": (0x03, 4),
    "es_es": (0x04, 5),
    "es_mx": (0x05, 6),
    "it_it": (0x06, 7),
    "nl_nl": (0x07, 8),
    "pt_pt": (0x08, 9),
    "sv_se": (0x09, 10),
    "ja_jp": (0x0A, 11),
    "ko_kr": (0x0B, 12),
    "zh_cn": (0x0C, 13),
    "zh_tw": (0x0D, 14),
    "cs_cz": (0x0E, 15),
    "pl_pl": (0x0F, 16),
    "pt_br": (0x10, 17),
    "ru_ru": (0x11, 18),
}

#: canonical locale instance order used by packaging tools
LOCALE_ORDER: list[str] = list(LOCALES.keys())


@dataclass
class StblEntry:
    key: int          # uint32, high-bit FNV-32
    text: str
    sid: int = 0
    line: int = 0
    flags: int = 0

    def to_dict(self) -> dict:
        return {"key": f"0x{self.key:08X}", "text": self.text,
                "sid": self.sid, "line": self.line}


@dataclass
class StblFile:
    """A parsed STBL resource."""

    language: int = 1          # en_us
    entries: list[StblEntry] = field(default_factory=list)

    def get(self, key: int) -> StblEntry | None:
        for e in self.entries:
            if e.key == key:
                return e
        return None

    def as_dict(self) -> dict[str, str]:
        return {f"0x{e.key:08X}": e.text for e in self.entries}


def stbl_instance(base_name: str, locale: str = "en_us") -> int:
    """64-bit STBL instance id: FNV-32(name) with locale code in high byte."""
    code = LOCALES[locale.lower()][0]
    return (code << 56) | fnv32(base_name, high_bit=False)


def make_key(reference: str) -> int:
    """STBL key for a ``file:string`` style reference (high-bit FNV-32)."""
    return fnv32(reference, high_bit=True)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_stbl(data: bytes) -> StblFile:
    r = io.BytesIO(data)
    magic = r.read(4)
    if magic != STBL_MAGIC:
        raise ValueError(f"Bad STBL magic {magic!r}")
    version, fmt, language, count, str_size = struct.unpack(
        "<IHHII", r.read(16))
    if version != STBL_VERSION:
        raise ValueError(f"Unsupported STBL version {version}")
    r.read(8)  # unused

    raw_entries = []
    for _ in range(count):
        key, flags, sid, line, slen, soff = struct.unpack(
            "<IB3xHHHI", r.read(18))
        raw_entries.append((key, flags, sid, line, slen, soff))

    # meta table (may be empty)
    remaining = len(data) - r.tell() - str_size
    if remaining > 0:
        meta_count = struct.unpack("<I", r.read(4))[0]
        r.read(8 * meta_count)

    strings = r.read(str_size)
    entries = []
    for key, flags, sid, line, slen, soff in raw_entries:
        chunk = strings[soff:soff + slen]
        if chunk.endswith(b"\x00"):
            chunk = chunk[:-1]
        entries.append(StblEntry(key=key, text=chunk.decode("utf-8"),
                                 sid=sid, line=line, flags=flags))
    return StblFile(language=language, entries=entries)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_stbl(stbl: StblFile) -> bytes:
    strings = io.BytesIO()
    offsets: list[tuple[int, int]] = []
    for e in stbl.entries:
        encoded = e.text.encode("utf-8") + b"\x00"
        offsets.append((strings.tell(), len(encoded)))
        strings.write(encoded)
    str_blob = strings.getvalue()

    out = io.BytesIO()
    out.write(STBL_MAGIC)
    out.write(struct.pack("<IHHII", STBL_VERSION, STBL_FORMAT,
                          stbl.language, len(stbl.entries), len(str_blob)))
    out.write(b"\x00" * 8)
    for e, (off, ln) in zip(stbl.entries, offsets):
        out.write(struct.pack("<IB3xHHHI", e.key, e.flags, e.sid, e.line,
                              ln, off))
        # 18 bytes: key(4) flags(1) pad(3) sid(2) line(2) length(2) offset(4)
    out.write(struct.pack("<I", 0))  # meta table: not emitted by mod tools
    out.write(str_blob)
    return out.getvalue()


def generate_locales(base_name: str,
                     strings: dict[str, dict[str, str]],
                     ) -> list[tuple[int, bytes]]:
    """Build all 18 locale STBL resources from one mapping.

    ``strings`` maps locale code -> {reference: text}.  Missing locales
    fall back to ``en_us``.  Returns ``[(instance, binary), ...]``.
    """
    fallback = strings.get("en_us", {})
    results = []
    for locale in LOCALE_ORDER:
        mapping = strings.get(locale) or fallback
        if not mapping:
            continue
        stbl = StblFile(
            language=LOCALES[locale][1],
            entries=[StblEntry(key=make_key(ref), text=text)
                     for ref, text in mapping.items()])
        results.append((stbl_instance(base_name, locale), build_stbl(stbl)))
    return results
