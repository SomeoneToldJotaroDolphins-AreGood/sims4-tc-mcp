"""FNV-1 hashing for The Sims 4 resource keys.

The Sims 4 uses Fowler-Noll-Vo **FNV-1** (multiply *then* XOR, not FNV-1a)
for String Table keys, tuning instance IDs and SimData table names.

Conventions implemented here:
- Input is lowercased before hashing (the game hashes case-insensitively).
- ``high_bit=True`` sets the sign bit of the hash (``| 0x8000...``).  For
  STBL keys and 32-bit tuning instances this marks the resource as
  modder-created; for 64-bit tuning instances it prevents collisions with
  Maxis content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# (bits -> (offset basis, prime)) for FNV-1
_FNV_PARAMS: dict[int, tuple[int, int]] = {
    24: (0x01C9DC5, 0x01000193),
    32: (0x811C9DC5, 0x01000193),
    64: (0xCBF29CE484222325, 0x100000001B3),
}


def _fnv(text: str, bits: int) -> int:
    mask = (1 << bits) - 1
    h, prime = _FNV_PARAMS[bits]
    for byte in text.encode("utf-8"):
        h = ((h * prime) & mask) ^ byte
    return h


_BITS = Literal[24, 32, 64]


def fnv24(text: str, high_bit: bool = False) -> int:
    """FNV-1 24-bit hash (used by some legacy SimData name keys)."""
    h = _fnv(text.lower(), 24)
    return h | 0x800000 if high_bit else h


def fnv32(text: str, high_bit: bool = False) -> int:
    """FNV-1 32-bit hash.  STBL keys use ``high_bit=True``."""
    h = _fnv(text.lower(), 32)
    return h | 0x80000000 if high_bit else h


def fnv64(text: str, high_bit: bool = True) -> int:
    """FNV-1 64-bit hash.  Custom tuning IDs use ``high_bit=True``."""
    h = _fnv(text.lower(), 64)
    return h | 0x8000000000000000 if high_bit else h


def hash_fnv(text: str, bits: _BITS = 64, high_bit: bool = True) -> int:
    """Hash ``text`` with FNV-1 at the requested width."""
    if bits == 24:
        return fnv24(text, high_bit)
    if bits == 32:
        return fnv32(text, high_bit)
    if bits == 64:
        return fnv64(text, high_bit)
    raise ValueError(f"Unsupported FNV width {bits!r} (use 24, 32 or 64)")


@dataclass(frozen=True)
class HashResult:
    """A hash rendered in every representation modders need."""

    input: str
    bits: int
    high_bit: bool
    decimal: int

    @property
    def hex(self) -> str:
        return f"0x{self.decimal:0{self.bits // 4}X}"

    @property
    def tgi_instance(self) -> str:
        """Value formatted for the instance slot of a TGI string."""
        return f"0x{self.decimal:016X}"

    def as_dict(self) -> dict[str, object]:
        return {
            "input": self.input,
            "bits": self.bits,
            "high_bit": self.high_bit,
            "dec": str(self.decimal),
            "hex": self.hex,
            "tgi_instance": self.tgi_instance,
        }


def format_hash(text: str, bits: _BITS = 64, high_bit: bool = True) -> HashResult:
    """Hash and return all representations."""
    return HashResult(
        input=text,
        bits=bits,
        high_bit=high_bit,
        decimal=hash_fnv(text, bits, high_bit),
    )
