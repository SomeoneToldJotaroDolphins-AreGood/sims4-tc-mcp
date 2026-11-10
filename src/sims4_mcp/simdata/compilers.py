"""Curated XML-tuning -> SimData compilers.

Full generic compilation from arbitrary XML is not feasible (the format
is community RE'd and table-specific), so this is an extensible registry
keyed by tuning class.  Each compiler maps a parsed tuning XML element to
a :class:`SimDataTable` whose TGI matches the tuning's instance.

Registered tables: ``TraitMappings`` + ``TraitDescriptions`` (traits),
``Buffs`` (buff display), ``AspirationMappings`` (aspirations),
``CareerTracks``/``Careers`` (careers), ``Venue``/``Lot`` tags,
``Objectives``.  Add new classes via :func:`register`.
"""

from __future__ import annotations

import re
from typing import Callable

from lxml import etree

from ..crypto.fnv import fnv32
from ..simdata.simdata import (SimDataField, SimDataFile, SimDataTable,
                               SimDataType)
from ..tdesc import tdesc

TUNING_NS = "http://www.maxis.com/schema/xmlTuning"
SIMS_NS = "http://www.nra3d.com/Sims"

Compiler = Callable[[etree._Element], SimDataTable | None]
_REGISTRY: dict[str, list[tuple[Compiler, str]]] = {}   # class -> [(fn, table)]


def register(class_name: str, table_name: str):
    def deco(fn: Compiler) -> Compiler:
        _REGISTRY.setdefault(class_name, []).append((fn, table_name))
        return fn
    return deco


def supported_classes() -> list[str]:
    return sorted(_REGISTRY)


def compile_simdata(root: etree._Element) -> list[SimDataTable]:
    """Compile all SimData tables required by a tuning XML document.

    ``root`` is the parsed tuning XML (any element in the document).
    Returns [] when the class needs no client binary data.
    """
    tables: list[SimDataTable] = []
    for el in root.iter():
        tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
        for fn, _name in _REGISTRY.get(tag, []):
            t = fn(el)
            if t is not None:
                tables.append(t)
    return tables


def simdata_tgi_for(table: SimDataTable, group: int = 0x1C000000,
                    ) -> tuple[int, int]:
    """(instance, group) TGI values for a SimData table resource.

    SimData instances are FNV-32 of the table name with the high bit set;
    the group mirrors the owning tuning's group.
    """
    return fnv32(table.name, high_bit=True), group


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _attr(el: etree._Element, name: str) -> str | None:
    return el.get(name)


def _child(el: etree._Element, name: str) -> etree._Element | None:
    for c in el.iter():
        if isinstance(c.tag, str) and etree.QName(c.tag).localname == name:
            return c
    return None


_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")


def _int_attr(el: etree._Element, name: str, default: int = 0) -> int:
    v = _attr(el, name)
    if v is None and ":" in name:
        v = _attr(el, f"{{{SIMS_NS}}}{name.split(':', 1)[1]}")
    if v is None:
        return default
    return int(v, 16) if _HEX_RE.match(v) else int(v)


def _id_attr(el: etree._Element) -> int:
    """The tuning's own i:id (namespaced or literal)."""
    v = _attr(el, f"{{{SIMS_NS}}}id", "i:id", "id")
    if v is None:
        return 0
    return int(v, 16) if _HEX_RE.match(v) else int(v)


# ---------------------------------------------------------------------------
# Trait -> TraitMappings + TraitDescriptions
# ---------------------------------------------------------------------------

@register("Trait", "TraitMappings")
def _compile_trait_mappings(trait_el: etree._Element) -> SimDataTable | None:
    trait_id = _int_attr(trait_el, "trait_id",
                         _int_attr(trait_el, "i:id") & 0xFFFFFFFF)
    if not trait_id:
        return None
    t = SimDataTable(
        name="TraitMappings",
        tdesc_instance=fnv32("Trait", high_bit=False),
        fields=[
            SimDataField("trait_id", SimDataType.UINT32, is_index=True),
            SimDataField("trait_name", SimDataType.STRING),
            SimDataField("trait_points", SimDataType.INT32),
            SimDataField("trait_type", SimDataType.UINT32),
        ],
        rows=[[trait_id,
               _attr(trait_el, "n") or "",
               _int_attr(trait_el, "trait_points", 1),
               _int_attr(trait_el, "trait_type", 0)]],
    )
    return t


@register("Trait", "TraitDescriptions")
def _compile_trait_descriptions(trait_el: etree._Element) -> SimDataTable | None:
    trait_id = _int_attr(trait_el, "trait_id",
                         _int_attr(trait_el, "i:id") & 0xFFFFFFFF)
    if not trait_id:
        return None
    name_key = _attr(trait_el, "trait_name") or ""
    desc_key = _attr(trait_el, "trait_description") or ""
    icon = _attr(trait_el, "trait_icon") or ""
    return SimDataTable(
        name="TraitDescriptions",
        tdesc_instance=fnv32("Trait", high_bit=False),
        fields=[
            SimDataField("trait_id", SimDataType.UINT32, is_index=True),
            SimDataField("trait_name", SimDataType.STRING),
            SimDataField("trait_description", SimDataType.STRING),
            SimDataField("trait_icon", SimDataType.STRING),
        ],
        rows=[[trait_id, name_key, desc_key, icon]],
    )


# ---------------------------------------------------------------------------
# Buff -> Buffs display table
# ---------------------------------------------------------------------------

@register("Buff", "Buffs")
def _compile_buff(buff_el: etree._Element) -> SimDataTable | None:
    buff_id = _int_attr(buff_el, "buff_id",
                        _int_attr(buff_el, "i:id") & 0xFFFFFFFF)
    if not buff_id:
        return None
    return SimDataTable(
        name="Buffs",
        tdesc_instance=fnv32("Buff", high_bit=False),
        fields=[
            SimDataField("buff_id", SimDataType.UINT32, is_index=True),
            SimDataField("buff_name", SimDataType.STRING),
            SimDataField("buff_description", SimDataType.STRING),
            SimDataField("buff_icon", SimDataType.STRING),
        ],
        rows=[[buff_id,
               _attr(buff_el, "buff_name") or _attr(buff_el, "n") or "",
               _attr(buff_el, "description") or "",
               _attr(buff_el, "icon") or "buff_satisfaction"]],
    )


# ---------------------------------------------------------------------------
# Aspiration
# ---------------------------------------------------------------------------

@register("Aspiration", "AspirationMappings")
def _compile_aspiration(asp_el: etree._Element) -> SimDataTable | None:
    asp_id = _int_attr(asp_el, "aspiration_id",
                       _int_attr(asp_el, "i:id") & 0xFFFFFFFF)
    if not asp_id:
        return None
    return SimDataTable(
        name="AspirationMappings",
        tdesc_instance=fnv32("Aspiration", high_bit=False),
        fields=[
            SimDataField("aspiration_id", SimDataType.UINT32, is_index=True),
            SimDataField("aspiration_name", SimDataType.STRING),
            SimDataField("aspiration_description", SimDataType.STRING),
            SimDataField("aspiration_icon", SimDataType.STRING),
        ],
        rows=[[asp_id,
               _attr(asp_el, "aspiration_name") or _attr(asp_el, "n") or "",
               _attr(asp_el, "aspiration_description") or "",
               _attr(asp_el, "aspiration_icon") or "aspiration_motive"]],
    )
