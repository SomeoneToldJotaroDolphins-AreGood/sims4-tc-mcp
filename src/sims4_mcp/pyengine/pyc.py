"""Python 3.7 ``.pyc`` header writer (magic ``3394``).

The Sims 4 embedded CPython 3.7.0 accepts pyc magic ``3394``
(``b'\\x42\\x0d\\x0d\\x0a'``).  Real bytecode cannot be produced from a
newer interpreter (marshal/opcode formats differ), so this module emits
the **header framing only** — useful for inspection tooling, embedded
resource bundles and tests.  ``.ts4script`` packages ship ``.py`` source
which the game compiles at runtime; that is the supported path.

CPython 3.7 pyc header (PEP 552, 16 bytes)::

    magic 4 bytes  (b'\\x42\\x0d\\x0d\\x0a')
    flags u32      (0 = timestamp based)
    mtime u32
    source size u32
"""

from __future__ import annotations

import struct

MAGIC_3394 = 3394
MAGIC_BYTES = struct.pack("<H", MAGIC_3394) + b"\x0d\x0a"
HEADER_SIZE = 16


def pyc_header(source_size: int, mtime: int = 0) -> bytes:
    """Serialize the 16-byte CPython 3.7 timestamp-based pyc header."""
    return MAGIC_BYTES + struct.pack(
        "<III", 0, mtime & 0xFFFFFFFF, source_size & 0xFFFFFFFF)


def write_pyc(path: str, code_bytes: bytes, source_size: int,
              mtime: int = 0) -> None:
    """Write header + provided marshal payload to ``path``."""
    with open(path, "wb") as fh:
        fh.write(pyc_header(source_size, mtime))
        fh.write(code_bytes)


def parse_pyc_header(data: bytes) -> dict:
    """Parse a pyc header; raises ValueError for non-3.7 magic."""
    if len(data) < HEADER_SIZE:
        raise ValueError("pyc file too short")
    if data[:4] != MAGIC_BYTES:
        magic = struct.unpack("<H", data[:2])[0]
        raise ValueError(
            f"pyc magic {magic} is not CPython 3.7.0 (expected 3394)")
    flags, mtime, size = struct.unpack("<III", data[4:16])
    return {"magic": MAGIC_3394, "flags": flags, "mtime": mtime,
            "source_size": size}
