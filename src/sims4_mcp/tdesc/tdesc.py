"""TDESC (Tuning Description) schema parser and query engine.

TDESC resources (type ``0x018C920A``) describe the tunable schema of every
Sims 4 tuning class: which XML attributes exist, their types, and which
fields are exported to the client as SimData binary rows.

This module parses ``.tdesc`` XML files (as dumped by Sims 4 Studio /
Lot51 extractors) and answers ``query_tdesc_schema`` lookups.  When
``SIMS4_TDESC_DIR`` is not configured, a small bundled registry of the
most common tuning classes keeps the tool useful for scaffolding.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .. import config

TDESC_NS = "http://www.maxis.com/schema/xmlTdesc"


@dataclass
class TdescField:
    name: str
    field_type: str          # primitive type or class reference
    export_mode: str         # server_xml | client_binary | all
    default: str | None = None
    enum_values: list[str] = field(default_factory=list)

    def is_client(self) -> bool:
        return self.export_mode in ("client_binary", "all")

    def as_dict(self) -> dict:
        d = {"name": self.name, "type": self.field_type,
             "export": self.export_mode}
        if self.default is not None:
            d["default"] = self.default
        if self.enum_values:
            d["enum"] = self.enum_values
        return d


@dataclass
class TdescClass:
    class_name: str
    super_class: str | None
    fields: list[TdescField] = field(default_factory=list)

    def client_fields(self) -> list[TdescField]:
        return [f for f in self.fields if f.is_client()]

    def as_dict(self) -> dict:
        return {
            "class": self.class_name,
            "super": self.super_class,
            "requires_simdata": bool(self.client_fields()),
            "fields": [f.as_dict() for f in self.fields],
        }


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

_EXPORT_MAP = {
    "Server": "server_xml",
    "Client": "client_binary",
    "Both": "all",
    "ServerOnly": "server_xml",
    "ClientOnly": "client_binary",
}


def _qn(tag: str) -> str:
    return f"{{{TDESC_NS}}}{tag}"


def parse_tdesc_xml(data: bytes | str) -> list[TdescClass]:
    """Parse one ``.tdesc`` file (may contain multiple class definitions)."""
    root = etree.fromstring(data if isinstance(data, (bytes, bytearray))
                            else data.encode("utf-8"))
    classes: list[TdescClass] = []
    for cls_el in root.iter(_qn("Class")):
        name = cls_el.get(_qn("name")) or cls_el.get("name") or ""
        super_name = (cls_el.get(_qn("super")) or cls_el.get("super")
                      or cls_el.get(_qn("base")) or None)
        tcls = TdescClass(class_name=name, super_class=super_name)
        for f_el in cls_el.iter(_qn("Field")):
            fname = f_el.get(_qn("name")) or f_el.get("name") or ""
            ftype = (f_el.get(_qn("type")) or f_el.get("type")
                     or f_el.get(_qn("dataType")) or "System.UInt32")
            export_raw = (f_el.get(_qn("export")) or f_el.get("export")
                          or "Server")
            export = _EXPORT_MAP.get(export_raw, "server_xml")
            enums = [e.get(_qn("name")) or e.get("name") or ""
                     for e in f_el.iter(_qn("EnumValue"))]
            tcls.fields.append(TdescField(
                name=fname, field_type=ftype, export_mode=export,
                default=f_el.get(_qn("default")) or f_el.get("default"),
                enum_values=[e for e in enums if e]))
        if tcls.class_name:
            classes.append(tcls)
    return classes


# ---------------------------------------------------------------------------
# Bundled fallback registry (curated subset for scaffolding without game
# files).  Keys are class names; values are TDESC-shaped dicts.
# ---------------------------------------------------------------------------

_BUNDLED: dict[str, dict] = {
    "Trait": {
        "class": "Trait", "super": "Tuning",
        "fields": [
            {"name": "trait_id", "type": "System.UInt32", "export": "all"},
            {"name": "trait_name", "type": "System.String", "export": "client_binary"},
            {"name": "trait_description", "type": "System.String", "export": "client_binary"},
            {"name": "trait_icon", "type": "System.String", "export": "client_binary"},
            {"name": "trait_categories", "type": "System.Collections.Generic.List", "export": "client_binary"},
            {"name": "trait_owners", "type": "System.Collections.Generic.List", "export": "server_xml"},
            {"name": "trait_is_opposite_of", "type": "System.Collections.Generic.List", "export": "server_xml"},
        ],
    },
    "Buff": {
        "class": "Buff", "super": "Tuning",
        "fields": [
            {"name": "buff_name", "type": "System.String", "export": "client_binary"},
            {"name": "description", "type": "System.String", "export": "client_binary"},
            {"name": "icon", "type": "System.String", "export": "client_binary"},
            {"name": "max_stack", "type": "System.Int32", "export": "server_xml"},
            {"name": "duration", "type": "System.Single", "export": "server_xml"},
            {"name": "buff_type", "type": "BuffType", "export": "server_xml"},
        ],
    },
    "SuperInteraction": {
        "class": "SuperInteraction", "super": "Interaction",
        "fields": [
            {"name": "interaction_name", "type": "System.String", "export": "client_binary"},
            {"name": "interaction_tooltip", "type": "System.String", "export": "client_binary"},
            {"name": "category", "type": "InteractionCategory", "export": "server_xml"},
            {"name": "autonomy_priority", "type": "System.Int32", "export": "server_xml"},
            {"name": "command", "type": "Command", "export": "server_xml"},
            {"name": "definition_data", "type": "DefinitionData", "export": "server_xml"},
            {"name": "loot", "type": "System.Collections.Generic.List", "export": "server_xml"},
            {"name": "tests", "type": "System.Collections.Generic.List", "export": "server_xml"},
        ],
    },
    "LootActions": {
        "class": "LootActions", "super": "Loot",
        "fields": [
            {"name": "loot_name", "type": "System.String", "export": "server_xml"},
            {"name": "actions", "type": "System.Collections.Generic.List", "export": "server_xml"},
        ],
    },
    "TestSetInstance": {
        "class": "TestSetInstance", "super": "Tuning",
        "fields": [
            {"name": "tests", "type": "System.Collections.Generic.List", "export": "server_xml"},
            {"name": "whitelist", "type": "System.Boolean", "export": "server_xml"},
        ],
    },
}


def _db_path() -> Path:
    return config.cache_dir() / "tdesc_index.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tdesc "
        "(class TEXT PRIMARY KEY, super TEXT, json TEXT)")
    return conn


def build_index(force: bool = False) -> int:
    """Index all ``.tdesc`` files from ``SIMS4_TDESC_DIR``.

    Returns the number of classes indexed.  Always merges the bundled
    registry so core classes are queryable without game files.
    """
    conn = _connect()
    try:
        if force:
            conn.execute("DELETE FROM tdesc")
        for cls in _BUNDLED.values():
            conn.execute(
                "INSERT OR IGNORE INTO tdesc(class, super, json) VALUES (?,?,?)",
                (cls["class"], cls.get("super"), json.dumps(cls)))
        count = conn.execute("SELECT COUNT(*) FROM tdesc").fetchone()[0]
        root = config.tdesc_dir()
        if root:
            for path in Path(root).rglob("*.tdesc"):
                try:
                    classes = parse_tdesc_xml(path.read_bytes())
                except (etree.XMLSyntaxError, OSError):
                    continue
                for c in classes:
                    conn.execute(
                        "INSERT OR REPLACE INTO tdesc(class, super, json)"
                        " VALUES (?,?,?)",
                        (c.class_name, c.super_class,
                         json.dumps(c.as_dict())))
            conn.commit()
            count = conn.execute("SELECT COUNT(*) FROM tdesc").fetchone()[0]
        return count
    finally:
        conn.close()


def query_class(class_name: str) -> dict | None:
    """Return the schema dict for a tuning class (case-insensitive)."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT json FROM tdesc WHERE class = ? COLLATE NOCASE",
            (class_name,)).fetchone()
        if row:
            return json.loads(row[0])
    finally:
        conn.close()
    # last resort: bundled
    cls = _BUNDLED.get(class_name)
    return dict(cls) if cls else None


def xml_skeleton(class_name: str) -> str | None:
    """Render a starter XML tuning document for a class from its schema."""
    schema = query_class(class_name)
    if not schema:
        return None
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<{class_name} '
        'xmlns="http://www.maxis.com/schema/xmlTuning" '
        'xmlns:sims="http://www.nra3d.com/Sims" '
        'i:id="0x8000000000000000" t="None" '
        'n="REPLACE_ME" s="REPLACE_ME">',
    ]
    for f in schema["fields"]:
        if f["export"] == "server_xml":
            lines.append(f'    <T n="{f["name"]}" value="0x0"/> '
                         f'<!-- {f["type"]} -->')
    lines += [f"</{class_name}>"]
    return "\n".join(lines)
