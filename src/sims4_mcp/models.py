"""Shared models and resource-type constants for The Sims 4 package formats."""

from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Well-known Sims 4 resource type IDs
# ---------------------------------------------------------------------------

TYPE_STBL = 0x220557DA          # String Table (binary)
TYPE_SIMDATA = 0x545AC67A       # SimData binary table (DATA)
TYPE_TDESC = 0x018C920A         # Tuning description schema
TYPE_CASP = 0x034AEECB          # CAS Part
TYPE_COBJ = 0x319E4F1D          # Catalog Object (COBJ)
TYPE_OBJD = 0xC0DB5AE7          # Object Definition (TLV)
TYPE_OLD_OBJD = 0x2D57B212      # legacy alias seen in old tooling
TYPE_CATALOG_SET = 0xFF56010C   # Object catalog set
TYPE_SCRIPT = 0x82F2D264        # Python script resource
TYPE_SCRIPTXML = 0xE3F10639     # Script XML (module map)
TYPE_TGL = 0x016604F0           # Tuning Global Load list
TYPE_TTAB = 0x02650542          # Tuning Table

TYPE_INTERACTION = 0xE882D22F   # SuperInteraction tuning
TYPE_BUFF = 0x6017E896          # Buff tuning
TYPE_TRAIT = 0xCB5FDDC7         # Trait tuning
TYPE_LOOT = 0x0C772E27          # LootActions tuning
TYPE_SNIPPET = 0x7DF2169C       # Snippet tuning
TYPE_TUNING_GENERIC = 0x03B33DDF  # Generic/module tuning

#: type id -> human readable name
TYPE_NAMES: dict[int, str] = {
    TYPE_STBL: "STBL",
    TYPE_SIMDATA: "SimData (DATA)",
    TYPE_TDESC: "TDESC",
    TYPE_CASP: "CASP",
    TYPE_COBJ: "COBJ",
    TYPE_OBJD: "OBJD",
    TYPE_CATALOG_SET: "CatalogSet",
    TYPE_SCRIPT: "Script",
    TYPE_SCRIPTXML: "ScriptXML",
    TYPE_TGL: "TGL",
    TYPE_TTAB: "TTAB",
    TYPE_INTERACTION: "Tuning:Interaction",
    TYPE_BUFF: "Tuning:Buff",
    TYPE_TRAIT: "Tuning:Trait",
    TYPE_LOOT: "Tuning:LootActions",
    TYPE_SNIPPET: "Tuning:Snippet",
    TYPE_TUNING_GENERIC: "Tuning:Generic",
}

#: Types whose payloads are tuning XML documents.
TUNING_TYPES: frozenset[int] = frozenset({
    TYPE_INTERACTION, TYPE_BUFF, TYPE_TRAIT, TYPE_LOOT,
    TYPE_SNIPPET, TYPE_TUNING_GENERIC,
})

_TGI_RE = re.compile(
    r"^0x(?P<type>[0-9a-fA-F]{1,8})[-_]0x(?P<group>[0-9a-fA-F]{1,8})"
    r"[-_]0x(?P<instance>[0-9a-fA-F]{1,16})$"
)


class TGI(BaseModel):
    """A Sims 4 resource key: Type/Group/Instance."""

    model_config = ConfigDict(frozen=True)

    type: int
    group: int
    instance: int

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (f"0x{self.type:08X}-0x{self.group:08X}"
                f"-0x{self.instance:016X}")

    @classmethod
    def from_string(cls, value: str) -> "TGI":
        """Parse ``0xTYPE-0xGROUP-0xINSTANCE`` (also accepts ``_`` separators)."""
        m = _TGI_RE.match(value.strip())
        if not m:
            raise ValueError(
                f"Invalid TGI string {value!r}; expected "
                "'0xTYPE-0xGROUP-0xINSTANCE'")
        return cls(
            type=int(m["type"], 16),
            group=int(m["group"], 16),
            instance=int(m["instance"], 16),
        )

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, f"Unknown(0x{self.type:08X})")

    @property
    def is_tuning(self) -> bool:
        return self.type in TUNING_TYPES
