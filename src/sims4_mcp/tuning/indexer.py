"""Vanilla tuning XML indexer (SQLite FTS5) + reverse-reference graph.

Walks ``SIMS4_EXTRACTED_TUNING_DIR`` (Maxis XML dumps from Sims 4 Studio
or Lot51 extractors) and builds ``tuning_index.db`` in the cache dir with:

- ``tuning``       — one row per tuning document (instance, name, class,
                     string key, path)
- ``tuning_fts``   — FTS5 virtual table over name/class/path for search
- ``tuning_refs``  — every ``<T ... value="0x...">`` / ``instance="0x..."``
                     reference: (source instance -> target instance, field)

The refs table powers ``get_tuning_dependency_graph`` so agents can see
what depends on a tuning before modifying or stripping it.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from lxml import etree

from .. import config
from ..crypto.fnv import fnv32

TUNING_NS = "http://www.maxis.com/schema/xmlTuning"
SIMS_NS = "http://www.nra3d.com/Sims"

_HEX_RE = re.compile(r"^0x([0-9a-fA-F]{1,16})$")

#: Tuning value/reference nodes — never indexed as tunings themselves.
_VALUE_TAGS = frozenset({"T", "L", "V", "A", "U", "S"})


def _db_path() -> Path:
    return config.cache_dir() / "tuning_index.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tuning (
            instance INTEGER PRIMARY KEY,
            name TEXT, string_key TEXT, class TEXT,
            type_id INTEGER, path TEXT, mtime REAL
        );
        CREATE TABLE IF NOT EXISTS tuning_refs (
            source INTEGER, target INTEGER, field TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_refs_target ON tuning_refs(target);
        CREATE INDEX IF NOT EXISTS idx_refs_source ON tuning_refs(source);
        CREATE VIRTUAL TABLE IF NOT EXISTS tuning_fts USING fts5(
            instance UNINDEXED, name, class, path
        );
    """)
    return conn


def _resolve_instance(el: etree._Element) -> int | None:
    """The tuning's own instance: i:id attr, or FNV of s= / n=."""
    for attr in (f"{{{SIMS_NS}}}id", "i:id", "id"):
        v = el.get(attr)
        if v and _HEX_RE.match(v):
            return int(v, 16)
    s = el.get("s")
    if s:
        return fnv32(s, high_bit=True)
    n = el.get("n")
    if n:
        return fnv32(n, high_bit=True)
    return None


def _extract_refs(el: etree._Element, source: int) -> list[tuple[int, int, str]]:
    """All integer tuning references inside a document."""
    refs = []
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        for attr in ("value", "instance", "id"):
            v = node.get(attr)
            if v and _HEX_RE.match(v):
                target = int(v, 16)
                if target and target != source:
                    fname = node.get("n") or etree.QName(node).localname
                    refs.append((source, target, fname))
    return refs


def index_file(conn: sqlite3.Connection, path: Path) -> int:
    """Index one XML file; returns number of tunings added."""
    try:
        tree = etree.parse(str(path))
    except (etree.XMLSyntaxError, OSError):
        return 0
    root = tree.getroot()
    added = 0
    mtime = path.stat().st_mtime
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el.tag).localname in _VALUE_TAGS:
            continue
        instance = _resolve_instance(el)
        if instance is None:
            continue
        cls = etree.QName(el).localname
        name = el.get("n") or ""
        skey = el.get("s") or ""
        type_id = fnv32(cls, high_bit=False)
        conn.execute(
            "INSERT OR REPLACE INTO tuning"
            "(instance, name, string_key, class, type_id, path, mtime)"
            " VALUES (?,?,?,?,?,?,?)",
            (instance, name, skey, cls, type_id, str(path), mtime))
        conn.execute("DELETE FROM tuning_fts WHERE instance = ?",
                     (str(instance),))
        conn.execute(
            "INSERT INTO tuning_fts(instance, name, class, path)"
            " VALUES (?,?,?,?)",
            (str(instance), name, cls, str(path)))
        conn.execute("DELETE FROM tuning_refs WHERE source=?", (instance,))
        refs = _extract_refs(el, instance)
        conn.executemany(
            "INSERT INTO tuning_refs(source, target, field) VALUES (?,?,?)",
            refs)
        added += 1
    return added


def build_index(force: bool = False) -> dict:
    """(Re)build the tuning index.  Incremental via mtime manifest."""
    root = config.extracted_tuning_dir()
    if not root:
        return {"status": "unconfigured",
                "hint": f"Set {_ENV_HINT} to extracted Maxis XML tuning dir"}
    conn = _connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS manifest (path TEXT PRIMARY KEY,"
        " mtime REAL)")
    try:
        if force:
            conn.execute("DELETE FROM tuning")
            conn.execute("DELETE FROM tuning_fts")
            conn.execute("DELETE FROM tuning_refs")
            conn.execute("DELETE FROM manifest")
            conn.commit()

        total = 0
        files = 0
        for path in Path(root).rglob("*.xml"):
            mtime = path.stat().st_mtime
            row = conn.execute(
                "SELECT mtime FROM manifest WHERE path=?",
                (str(path),)).fetchone()
            if row and abs(row[0] - mtime) < 1e-6:
                continue
            n = index_file(conn, path)
            conn.execute(
                "INSERT OR REPLACE INTO manifest(path, mtime) VALUES (?,?)",
                (str(path), mtime))
            total += n
            files += 1
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM tuning").fetchone()[0]
        return {"status": "ok", "files_processed": files,
                "tunings_indexed": count}
    finally:
        conn.close()


_ENV_HINT = "SIMS4_EXTRACTED_TUNING_DIR"


def index_status() -> dict:
    root = config.extracted_tuning_dir()
    if not _db_path().exists():
        return {"indexed": False, "tunings": 0,
                "source": str(root) if root else None}
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM tuning").fetchone()[0]
    finally:
        conn.close()
    return {"indexed": True, "tunings": count,
            "source": str(root) if root else None,
            "db": str(_db_path())}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def search(query: str, class_filter: str | None = None,
           limit: int = 20) -> list[dict]:
    conn = _connect()
    try:
        q = f'"{query}"' if " " in query else query
        sql = ("SELECT t.instance, t.name, t.class, t.path FROM tuning t "
               "JOIN tuning_fts f ON t.instance = f.instance "
               "WHERE tuning_fts MATCH ?")
        params: list = [q]
        if class_filter:
            sql += " AND t.class = ? COLLATE NOCASE"
            params.append(class_filter)
        sql += " LIMIT ?"
        params.append(limit)
        rows: list = []
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            # FTS tokenization (e.g. buff_test) or syntax error -> LIKE
            like_sql = ("SELECT instance, name, class, path FROM tuning "
                        "WHERE (name LIKE ? OR string_key LIKE ? "
                        "OR class LIKE ?)")
            like_params: list = [f"%{query}%", f"%{query}%", f"%{query}%"]
            if class_filter:
                like_sql += " AND class = ? COLLATE NOCASE"
                like_params.append(class_filter)
            like_sql += " LIMIT ?"
            like_params.append(limit)
            rows = conn.execute(like_sql, like_params).fetchall()
        return [{"instance": f"0x{r[0]:016X}", "name": r[1],
                 "class": r[2], "path": r[3]} for r in rows]
    finally:
        conn.close()


def read_tuning(instance: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT path, name, class FROM tuning WHERE instance = ?",
            (instance,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    path, name, cls = row
    try:
        xml = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        xml = None
    return {"instance": f"0x{instance:016X}", "name": name, "class": cls,
            "path": path, "xml": xml}


def dependency_graph(instance: int, direction: str = "both",
                     depth: int = 2) -> dict:
    """BFS over tuning_refs in the requested direction."""
    conn = _connect()
    try:
        def _label(i: int) -> dict:
            row = conn.execute(
                "SELECT name, class FROM tuning WHERE instance=?",
                (i,)).fetchone()
            return {"instance": f"0x{i:016X}",
                    "name": row[0] if row else None,
                    "class": row[1] if row else "Unknown"}

        def _bfs(start: int, out: bool) -> list[dict]:
            frontier = {start}
            seen: set[int] = set()
            edges = []
            for _ in range(depth):
                if not frontier:
                    break
                ph = ",".join("?" * len(frontier))
                if out:
                    rows = conn.execute(
                        f"SELECT source, target, field FROM tuning_refs "
                        f"WHERE source IN ({ph})", tuple(frontier)).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT source, target, field FROM tuning_refs "
                        f"WHERE target IN ({ph})", tuple(frontier)).fetchall()
                nxt = set()
                for s, t, field in rows:
                    edges.append({"from": f"0x{s:016X}",
                                  "to": f"0x{t:016X}", "field": field})
                    other = t if out else s
                    if other not in seen and other != instance:
                        seen.add(other)
                        nxt.add(other)
                frontier = nxt
            return edges

        result: dict = {"root": _label(instance)}
        if direction in ("outgoing", "both"):
            result["depends_on"] = _bfs(instance, out=True)
        if direction in ("incoming", "both"):
            result["depended_by"] = _bfs(instance, out=False)
        return result
    finally:
        conn.close()
