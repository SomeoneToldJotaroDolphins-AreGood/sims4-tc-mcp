"""AST indexer for decompiled Maxis game Python.

Walks ``SIMS4_DECOMPILED_SCRIPTS_DIR`` (``base``, ``core``,
``simulation``, ``protocolbuffers`` etc. decompiled with the community
toolchain) and stores classes (with bases for MRO), methods (signatures +
docstrings) and module-level singletons in ``game_python.db`` with FTS.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from .. import config


def _db_path() -> Path:
    return config.cache_dir() / "game_python.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY,
            module TEXT, name TEXT, bases TEXT, doc TEXT, path TEXT
        );
        CREATE TABLE IF NOT EXISTS methods (
            id INTEGER PRIMARY KEY,
            class_id INTEGER, module TEXT, class_name TEXT,
            name TEXT, signature TEXT, doc TEXT, decorators TEXT, path TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_methods_class ON methods(class_name);
        CREATE VIRTUAL TABLE IF NOT EXISTS py_fts USING fts5(
            kind UNINDEXED, module, name, class_name, doc, signature
        );
    """)
    return conn


def _sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(fn.args)
    except Exception:
        args = "..."
    return f"{fn.name}({args})"


def index_file(conn: sqlite3.Connection, path: Path) -> int:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (SyntaxError, OSError, UnicodeDecodeError):
        return 0
    module = ".".join(path.with_suffix("").parts[-2:])
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            conn.execute(
                "INSERT INTO classes(module, name, bases, doc, path)"
                " VALUES (?,?,?,?,?)",
                (module, node.name, bases,
                 ast.get_docstring(node) or "", str(path)))
            class_id = conn.execute(
                "SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO py_fts(kind, module, name, class_name, doc,"
                " signature) VALUES ('class', ?, ?, '', ?, '')",
                (module, node.name, ast.get_docstring(node) or ""))
            count += 1
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decos = ", ".join(ast.unparse(d) for d in item.decorator_list)
                    conn.execute(
                        "INSERT INTO methods(class_id, module, class_name,"
                        " name, signature, doc, decorators, path)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (class_id, module, node.name, item.name,
                         _sig(item), ast.get_docstring(item) or "",
                         decos, str(path)))
                    conn.execute(
                        "INSERT INTO py_fts(kind, module, name, class_name,"
                        " doc, signature) VALUES ('method', ?, ?, ?, ?, ?)",
                        (module, item.name, node.name,
                         ast.get_docstring(item) or "", _sig(item)))
                    count += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # module-level functions
            parent = _find_parent_class(tree, node)
            if parent is None:
                conn.execute(
                    "INSERT INTO methods(class_id, module, class_name,"
                    " name, signature, doc, decorators, path)"
                    " VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                    (module, "", node.name, _sig(node),
                     ast.get_docstring(node) or "", "", str(path)))
                conn.execute(
                    "INSERT INTO py_fts(kind, module, name, class_name,"
                    " doc, signature) VALUES ('function', ?, ?, '', ?, ?)",
                    (module, node.name, ast.get_docstring(node) or "",
                     _sig(node)))
                count += 1
    return count


def _find_parent_class(tree: ast.AST, fn) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if item is fn:
                    return node
    return None


def build_index(force: bool = False) -> dict:
    root = config.decompiled_scripts_dir()
    if not root:
        return {"status": "unconfigured",
                "hint": "Set SIMS4_DECOMPILED_SCRIPTS_DIR to decompiled "
                        "game Python source"}
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS manifest (path TEXT PRIMARY KEY,"
            " mtime REAL)")
        if force:
            for t in ("classes", "methods", "py_fts", "manifest"):
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        files = 0
        for path in Path(root).rglob("*.py"):
            mtime = path.stat().st_mtime
            row = conn.execute("SELECT mtime FROM manifest WHERE path=?",
                               (str(path),)).fetchone()
            if row and abs(row[0] - mtime) < 1e-6:
                continue
            index_file(conn, path)
            conn.execute(
                "INSERT OR REPLACE INTO manifest(path, mtime) VALUES (?,?)",
                (str(path), mtime))
            files += 1
        conn.commit()
        n_cls = conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
        n_m = conn.execute("SELECT COUNT(*) FROM methods").fetchone()[0]
        return {"status": "ok", "files_processed": files,
                "classes": n_cls, "methods": n_m}
    finally:
        conn.close()


def search(query: str, limit: int = 25) -> dict:
    conn = _connect()
    try:
        rows = []
        try:
            rows = conn.execute(
                "SELECT kind, module, name, class_name, signature, doc "
                "FROM py_fts WHERE py_fts MATCH ? LIMIT ?",
                (query, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:
            rows = conn.execute(
                "SELECT kind, module, name, class_name, signature, doc "
                "FROM py_fts WHERE name LIKE ? OR class_name LIKE ?"
                " OR doc LIKE ? LIMIT ?",
                (f"%{query}%", f"%{query}%", f"%{query}%",
                 limit)).fetchall()
        return {"results": [{"kind": r[0], "module": r[1], "name": r[2],
                             "class": r[3], "signature": r[4],
                             "doc": (r[5] or "")[:200]} for r in rows]}
    finally:
        conn.close()


def class_mro(class_name: str) -> dict:
    """Flatten the inheritance chain for a class from the index."""
    conn = _connect()
    try:
        chain: list[str] = [class_name]
        current = class_name
        methods: list[dict] = []
        while current:
            row = conn.execute(
                "SELECT bases FROM classes WHERE name = ? COLLATE NOCASE"
                " LIMIT 1", (current,)).fetchone()
            if not row or not row[0]:
                break
            bases = [b.strip() for b in row[0].split(",")]
            current = bases[0] if bases else None
            if current and current not in chain:
                chain.append(current)
        for cls in chain:
            for m in conn.execute(
                    "SELECT name, signature, doc FROM methods"
                    " WHERE class_name = ? COLLATE NOCASE", (cls,)):
                methods.append({"class": cls, "method": m[0],
                                "signature": m[1],
                                "doc": (m[2] or "")[:160]})
        return {"class": class_name, "mro": chain, "methods": methods}
    finally:
        conn.close()
