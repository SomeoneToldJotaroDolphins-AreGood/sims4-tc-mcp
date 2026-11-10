"""Sims 4 exception diagnostics.

Parses ``lastException.txt``, ``lastUIException.txt`` and
``lastCrash.txt`` from the user data directory, strips the XML wrapper
noise (``<root><report>...`` / ``<root><traceback>...``), extracts the
Python traceback frames, and cross-references them against the indexed
game Python (``pyengine``) and vanilla tuning (``tuning``) databases to
name the offending module / class / tuning.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

EXCEPTION_FILES = ("lastException.txt", "lastUIException.txt",
                   "lastCrash.txt")

_FRAME_RE = re.compile(
    r'File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+'
    r'in\s+(?P<func>\S+)')
_TUNING_RE = re.compile(
    r'(?:Error loading tuning|Invalid tuning|Tuning ID)\s*[:=]?\s*'
    r'(?P<tuning>0x[0-9a-fA-F]{1,16}|[A-Za-z0-9_:.\-]+)')
_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>|<\?[^>]*\?>|<!\[CDATA\[|\]\]>")


@dataclass
class TraceFrame:
    file: str
    line: int
    function: str


@dataclass
class ExceptionReport:
    source_file: str
    exception_type: str | None = None
    message: str | None = None
    frames: list[TraceFrame] = field(default_factory=list)
    tuning_refs: list[str] = field(default_factory=list)
    suspects: list[dict] = field(default_factory=list)
    raw: str = ""

    def as_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "exception_type": self.exception_type,
            "message": self.message,
            "frames": [{"file": f.file, "line": f.line,
                        "function": f.function} for f in self.frames],
            "tuning_refs": self.tuning_refs,
            "suspects": self.suspects,
        }


def _find_latest(user_dir: Path | None) -> Path | None:
    if user_dir is None:
        return None
    best: Path | None = None
    best_mtime = -1.0
    for name in EXCEPTION_FILES:
        candidate = user_dir / name
        if candidate.is_file():
            mtime = candidate.stat().st_mtime
            if mtime > best_mtime:
                best, best_mtime = candidate, mtime
    return best


def parse_exception_text(text: str, source_file: str = "") -> ExceptionReport:
    """Parse raw exception file content (XML wrapper tolerated)."""
    # unescape XML entities, then strip tags keeping their text content
    cleaned = html.unescape(text)
    cleaned = cleaned.replace("&#10;", "\n").replace("&quot;", '"')
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

    frames = [TraceFrame(file=m["file"], line=int(m["line"]),
                         function=m["func"])
              for m in _FRAME_RE.finditer(cleaned)]

    exc_type = None
    message = None
    # last line like: "AttributeError: 'NoneType' object has no attribute"
    for line in reversed(cleaned.splitlines()):
        line = line.strip()
        m = re.match(r"^([A-Za-z_][\w.]*?(?:Error|Exception|Warning))"
                     r":\s*(.*)$", line)
        if m:
            exc_type, message = m.group(1), m.group(2)
            break

    tuning_refs = [m.group("tuning") for m in _TUNING_RE.finditer(cleaned)]
    report = ExceptionReport(
        source_file=source_file, exception_type=exc_type, message=message,
        frames=frames, tuning_refs=tuning_refs, raw=cleaned)
    report.suspects = _cross_reference(report)
    return report


def _cross_reference(report: ExceptionReport) -> list[dict]:
    suspects: list[dict] = []
    try:
        from ..pyengine import astindex
        for frame in report.frames[:10]:
            module = Path(frame.file).stem
            matches = astindex.search(module, limit=3)
            for r in matches.get("results", []):
                suspects.append({
                    "kind": "game_python",
                    "file": frame.file,
                    "line": frame.line,
                    "function": frame.function,
                    "match": f"{r['module']}.{r['class'] or ''}"
                             f"{'.' if r['class'] else ''}{r['name']}",
                })
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        pass
    try:
        from ..tuning import indexer
        for ref in report.tuning_refs:
            if ref.lower().startswith("0x"):
                inst = int(ref, 16)
                row = indexer.read_tuning(inst)
                if row:
                    suspects.append({"kind": "tuning", "instance": ref,
                                     "name": row["name"],
                                     "class": row["class"]})
            else:
                for r in indexer.search(ref, limit=3):
                    suspects.append({"kind": "tuning",
                                     "instance": r["instance"],
                                     "name": r["name"],
                                     "class": r["class"]})
    except Exception:  # noqa: BLE001
        pass
    return suspects


def analyze_last_exception(sims4_docs_dir: str | None = None) -> dict:
    """Locate and analyze the newest exception file."""
    if sims4_docs_dir:
        user_dir = Path(sims4_docs_dir).expanduser()
    else:
        user_dir = config.sims4_user_dir()
    latest = _find_latest(user_dir)
    if latest is None:
        return {
            "found": False,
            "searched": str(user_dir) if user_dir else None,
            "hint": "Set SIMS4_USER_DIR to "
                    "'.../Documents/Electronic Arts/The Sims 4'",
        }
    text = latest.read_text(encoding="utf-8", errors="replace")
    report = parse_exception_text(text, source_file=str(latest))
    result = report.as_dict()
    result["found"] = True
    result["hint"] = _hint(report)
    return result


def _hint(report: ExceptionReport) -> str:
    if report.tuning_refs:
        return ("Exception mentions tuning resource(s); check the listed "
                "IDs with read_vanilla_tuning / search_vanilla_tuning.")
    if report.suspects:
        return ("Traceback frames matched indexed game code; inspect the "
                "suspects list for the failing module.")
    if report.frames:
        return ("Pure script traceback; check custom .ts4script modules in "
                "the frames (look for non-game module names).")
    return "No Python frames found; the file may contain only XML noise."
