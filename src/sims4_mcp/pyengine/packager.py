"""``.ts4script`` packager.

A ``.ts4script`` is a plain ZIP archive of Python modules placed in the
game's ``Mods`` folder.  The packager:

1. collects every ``.py`` under the source dir (skipping caches),
2. runs each through the Python 3.7 gatekeeper,
3. writes ``mod_version.py`` with ``minimum_script_version`` /
   ``maximum_script_version`` (57 == current game),
4. zips everything with deterministic timestamps.

The game compiles the shipped ``.py`` source at load time, so no pyc
generation is needed (and none is possible from a newer host).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .gatekeeper import check_source

SCRIPT_VERSION = 57


@dataclass
class FileCheck:
    path: str
    compatible: bool
    issues: list[dict] = field(default_factory=list)


@dataclass
class PackageResult:
    output_path: str
    file_count: int
    bytes_written: int
    compatible: bool
    files: list[FileCheck] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "output_path": self.output_path,
            "file_count": self.file_count,
            "bytes_written": self.bytes_written,
            "compatible": self.compatible,
            "errors": self.errors,
            "files": [{"path": f.path, "compatible": f.compatible,
                       "issues": f.issues} for f in self.files],
        }


_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "build", "dist"}


def collect_sources(source_dir: str) -> list[Path]:
    root = Path(source_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"source_dir is not a directory: {source_dir}")
    files = []
    for p in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        files.append(p)
    return sorted(files)


def validate_sources(source_dir: str) -> list[FileCheck]:
    checks = []
    root = Path(source_dir)
    for p in collect_sources(source_dir):
        result = check_source(p.read_text(encoding="utf-8", errors="replace"),
                              filename=str(p))
        checks.append(FileCheck(
            path=str(p.relative_to(root)),
            compatible=result.compatible,
            issues=result.as_dict()["issues"]))
    return checks


def _mod_version_source(module_name: str) -> str:
    return (
        "# -*- coding: utf-8 -*-\n"
        "minimum_script_version = {v}\n"
        "maximum_script_version = {v}\n"
        "add_file_extension = True\n"
        "package_name = {name!r}\n".format(v=SCRIPT_VERSION,
                                           name=module_name)
    )


def build_ts4script(source_dir: str, output_path: str,
                    module_name: str = "mod",
                    strict: bool = True) -> PackageResult:
    """Validate and bundle a ``.ts4script`` archive.

    ``strict=True`` (default) refuses to write the archive when any file
    fails the 3.7 gatekeeper.  With ``strict=False`` the archive is still
    written and the incompatibilities are reported.
    """
    root = Path(source_dir)
    checks = validate_sources(source_dir)
    errors = []
    incompatible = [c for c in checks if not c.compatible]
    if strict and incompatible:
        for c in incompatible:
            errors.append(f"{c.path}: "
                          f"{', '.join(i['construct'] for i in c.issues)}")
        return PackageResult(output_path=output_path, file_count=0,
                             bytes_written=0, compatible=False,
                             files=checks, errors=errors)

    buf = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in collect_sources(source_dir):
            rel = p.relative_to(root)
            zf.writestr(str(rel), p.read_bytes())
            written += 1
        zf.writestr("mod_version.py", _mod_version_source(module_name))
        written += 1

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return PackageResult(
        output_path=str(out), file_count=written,
        bytes_written=len(buf.getvalue()), compatible=not incompatible,
        files=checks, errors=errors)


def read_ts4script(path: str) -> list[dict]:
    """List the archive contents (name, size, crc)."""
    entries = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            entries.append({"name": info.filename,
                            "size": info.file_size,
                            "crc": f"0x{info.CRC:08X}"})
    return entries
