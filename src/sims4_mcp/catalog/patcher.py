"""Batch catalog override pipeline.

Reads one or more source packages, filters CASP/COBJ/OBJD entries, patches
spawn/catalog flags, and writes a new override package (identical TGIs, so
the game treats it as an override).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ..dbpf.package import PackageEntry, build_package, parse_package
from ..models import (TGI, TYPE_CASP, TYPE_COBJ, TYPE_OBJD, TYPE_NAMES)
from . import casp, cobj, objd

TYPE_ALIASES: dict[str, int] = {
    "casp": TYPE_CASP, "caspart": TYPE_CASP,
    "cobj": TYPE_COBJ, "objd": TYPE_OBJD,
}


@dataclass
class CatalogPatchResult:
    output_package: str
    scanned: int = 0
    patched: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "output_package": self.output_package,
            "scanned": self.scanned,
            "patched": self.patched,
            "skipped": self.skipped,
            "errors": self.errors,
            "details": self.details,
        }


def _matches(entry: PackageEntry, filter_query: str | None) -> bool:
    """Case-insensitive substring match over name / TGI / decoded CASP name."""
    if not filter_query:
        return True
    q = filter_query.lower()
    if q in str(entry.tgi).lower():
        return True
    if entry.name and q in entry.name.lower():
        return True
    if entry.tgi.type == TYPE_CASP:
        try:
            info = casp.parse_casp(entry.data)
            if q in info.name.lower():
                return True
        except casp.CaspError:
            pass
    return False


def batch_create_catalog_overrides(
        resource_type: str,
        filter_query: str,
        disable_random_spawn: bool,
        output_package: str,
        source_packages: list[str] | None = None,
        source_dir: str | None = None,
        hide_items: bool = False,
) -> CatalogPatchResult:
    """Patch CASP/COBJ/OBJD resources into a new override package.

    ``source_packages`` may point at any number of ``.package`` files; when
    omitted, ``source_dir`` is scanned for ``*.package``.
    """
    type_id = TYPE_ALIASES.get(resource_type.lower())
    if type_id is None:
        raise ValueError(
            f"resource_type must be one of {sorted(TYPE_ALIASES)}")

    sources: list[Path] = []
    if source_packages:
        sources = [Path(p) for p in source_packages]
    elif source_dir:
        sources = sorted(Path(source_dir).rglob("*.package"))
    if not sources:
        raise ValueError("no source packages supplied")

    result = CatalogPatchResult(output_package=output_package)
    out_entries: list[PackageEntry] = []
    seen: set[TGI] = set()

    for pkg_path in sources:
        try:
            pkg = parse_package(pkg_path.read_bytes())
        except Exception as e:  # noqa: BLE001 - report and continue
            result.errors.append(f"{pkg_path}: {e}")
            continue
        for entry in pkg.entries:
            result.scanned += 1
            if entry.tgi.type != type_id or entry.tgi in seen:
                continue
            if not _matches(entry, filter_query):
                result.skipped += 1
                continue
            try:
                data, detail = _patch_entry(
                    entry, disable_random_spawn, hide_items)
            except Exception as e:  # noqa: BLE001
                result.errors.append(
                    f"{entry.tgi}: {type(e).__name__}: {e}")
                continue
            if data is None:
                result.skipped += 1
                continue
            out_entries.append(PackageEntry(
                tgi=entry.tgi, data=bytes(data), name=entry.name))
            seen.add(entry.tgi)
            result.patched += 1
            result.details.append({"tgi": str(entry.tgi), **detail})

    if not out_entries:
        result.errors.append("no entries matched; nothing written")
        return result

    blob = build_package(out_entries)
    out = Path(output_package)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return result


def _patch_entry(entry: PackageEntry, disable_random: bool,
                 hide: bool) -> tuple[bytearray | None, dict]:
    data = bytearray(entry.data)
    if entry.tgi.type == TYPE_CASP:
        return casp.patch_casp_flags(
            data,
            allow_for_random=False if disable_random else None,
            show_in_ui=False if hide else None)
    if entry.tgi.type == TYPE_COBJ:
        return cobj.patch_cobj_flags(
            data,
            allow_for_random=False if disable_random else None,
            show_in_ui=False if hide else None)
    if entry.tgi.type == TYPE_OBJD:
        info = objd.parse_objd(bytes(data))
        return data, {"objd": info.as_dict()}
    return None, {}


def inspect_catalog_resource(data: bytes, type_id: int) -> dict:
    """Decode a catalog resource for ``extract_package_resource``."""
    if type_id == TYPE_CASP:
        return casp.parse_casp(data).as_dict()
    if type_id == TYPE_COBJ:
        return cobj.parse_cobj(data).as_dict()
    if type_id == TYPE_OBJD:
        return objd.parse_objd(data).as_dict()
    raise ValueError(f"not a catalog type: 0x{type_id:08X} "
                     f"({TYPE_NAMES.get(type_id, 'unknown')})")
