"""Project-directory compilation helpers.

A ``project_dir`` passed to ``build_package`` may contain:

- ``*.xml`` tuning documents.  The instance comes from ``i:id`` or a
  high-bit FNV-64 of ``s=``/``n=``; the resource type comes from the
  class name (:data:`CLASS_TYPE_MAP`) or an explicit ``type="0x..."``
  attribute.  Client-binary classes automatically get companion SimData.
- ``*.stbl.json`` string dictionaries::

      {"base_name": "mymod",
       "locales": {"en_us": {"ref:string": "Text"},
                   "de_de": {"ref:string": "Text"}}}

- ``*.bin`` binary resources with a sidecar ``*.tgi`` file containing
  ``0xTYPE-0xGROUP-0xINSTANCE``.

Everything is assembled into one DBPF 2.1 package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .crypto.fnv import fnv32, fnv64
from .dbpf.package import PackageEntry, build_package, write_package, Package
from .models import (TGI, TUNING_TYPES, TYPE_BUFF, TYPE_INTERACTION,
                     TYPE_LOOT, TYPE_SIMDATA, TYPE_SNIPPET, TYPE_TRAIT,
                     TYPE_TUNING_GENERIC)
from .simdata.compilers import compile_simdata, simdata_tgi_for
from .simdata.simdata import build_simdata, SimDataFile
from .stbl.stbl import generate_locales

TUNING_NS = "http://www.maxis.com/schema/xmlTuning"
SIMS_NS = "http://www.nra3d.com/Sims"
DEFAULT_GROUP = 0x1C000000

CLASS_TYPE_MAP: dict[str, int] = {
    "Buff": TYPE_BUFF,
    "Trait": TYPE_TRAIT,
    "SuperInteraction": TYPE_INTERACTION,
    "Interaction": TYPE_INTERACTION,
    "LootActions": TYPE_LOOT,
    "Snippet": TYPE_SNIPPET,
}


@dataclass
class TuningInfo:
    class_name: str
    instance: int
    group: int
    type_id: int
    name: str
    string_key: str
    root: etree._Element

    @property
    def tgi(self) -> TGI:
        return TGI(type=self.type_id, group=self.group,
                   instance=self.instance)


@dataclass
class BuildReport:
    entries: list[PackageEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tuning: list[TuningInfo] = field(default_factory=list)
    simdata_tables: list[str] = field(default_factory=list)
    stbl_resources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "entry_count": len(self.entries),
            "tuning": [{"class": t.class_name,
                        "tgi": str(t.tgi),
                        "name": t.name} for t in self.tuning],
            "simdata_tables": self.simdata_tables,
            "stbl_resources": self.stbl_resources,
            "warnings": self.warnings,
        }


def _get_attr(el: etree._Element, *names: str) -> str | None:
    for n in names:
        v = el.get(n)
        if v:
            return v
    return None


def _hexint(value: str) -> int:
    return int(value, 16) if value.lower().startswith("0x") else int(value)


def parse_tuning_info(xml_bytes: bytes) -> TuningInfo:
    root = etree.fromstring(xml_bytes)
    class_name = etree.QName(root).localname
    instance_attr = _get_attr(root, f"{{{SIMS_NS}}}id", "i:id", "id")
    if instance_attr:
        instance = _hexint(instance_attr)
    else:
        skey = _get_attr(root, "s")
        name = _get_attr(root, "n")
        if skey:
            instance = fnv64(skey, high_bit=True)
        elif name:
            instance = fnv64(name, high_bit=True)
        else:
            raise ValueError(
                f"tuning <{class_name}> has no i:id, s= or n= attribute")
    explicit_type = _get_attr(root, "type")
    type_id = (_hexint(explicit_type) if explicit_type
               else CLASS_TYPE_MAP.get(class_name, TYPE_TUNING_GENERIC))
    group = DEFAULT_GROUP
    group_attr = _get_attr(root, "group")
    if group_attr:
        group = _hexint(group_attr)
    return TuningInfo(
        class_name=class_name, instance=instance, group=group,
        type_id=type_id, name=_get_attr(root, "n") or "",
        string_key=_get_attr(root, "s") or "", root=root)


def compile_tuning(xml_bytes: bytes) -> BuildReport:
    """Compile one tuning document (XML + companion SimData) to entries."""
    info = parse_tuning_info(xml_bytes)
    report = BuildReport(tuning=[info])
    report.entries.append(PackageEntry(tgi=info.tgi, data=xml_bytes,
                                       name=info.name or None))
    tables = compile_simdata(info.root)
    if tables:
        sim = SimDataFile(tables=tables)
        blob = build_simdata(sim)
        instance, group = simdata_tgi_for(tables[0], info.group)
        report.entries.append(PackageEntry(
            tgi=TGI(type=TYPE_SIMDATA, group=group, instance=instance),
            data=blob))
        report.simdata_tables = [t.name for t in tables]
    return report


def _load_stbl_json(path: Path, report: BuildReport) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    base_name = payload.get("base_name") or path.stem
    locales = payload.get("locales")
    if not locales:
        # flat form: {"ref": "text"} -> en_us only
        locales = {"en_us": payload.get("strings", {})}
    generated = generate_locales(base_name, locales)
    for instance, blob in generated:
        report.entries.append(PackageEntry(
            tgi=TGI(type=0x220557DA, group=0xFFFFFFFF, instance=instance),
            data=blob))
    report.stbl_resources.extend(
        f"0x{inst:016X}" for inst, _ in generated)


def build_project(project_dir: str) -> BuildReport:
    root = Path(project_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"project_dir not found: {project_dir}")
    report = BuildReport()
    seen: set[TGI] = set()

    for path in sorted(root.rglob("*.xml")):
        try:
            tinfo = compile_tuning(path.read_bytes())
        except (etree.XMLSyntaxError, ValueError) as e:
            report.warnings.append(f"{path.name}: {e}")
            continue
        for entry in tinfo.entries:
            if entry.tgi in seen:
                report.warnings.append(
                    f"{path.name}: duplicate TGI {entry.tgi}; skipped")
                continue
            seen.add(entry.tgi)
            report.entries.append(entry)
        report.tuning.extend(tinfo.tuning)
        report.simdata_tables.extend(tinfo.simdata_tables)

    for path in sorted(root.rglob("*.stbl.json")):
        try:
            _load_stbl_json(path, report)
        except (json.JSONDecodeError, OSError) as e:
            report.warnings.append(f"{path.name}: {e}")

    for path in sorted(root.rglob("*.bin")):
        sidecar = path.with_suffix(".tgi")
        if not sidecar.is_file():
            report.warnings.append(
                f"{path.name}: missing sidecar {sidecar.name}")
            continue
        try:
            tgi = TGI.from_string(sidecar.read_text().strip())
        except ValueError as e:
            report.warnings.append(f"{path.name}: {e}")
            continue
        if tgi in seen:
            report.warnings.append(f"{path.name}: duplicate TGI {tgi}")
            continue
        seen.add(tgi)
        report.entries.append(PackageEntry(tgi=tgi, data=path.read_bytes()))

    if not report.entries:
        raise ValueError(f"no buildable resources found in {project_dir}")
    return report


def write_project_package(project_dir: str, output_path: str) -> BuildReport:
    report = build_project(project_dir)
    blob = build_package(report.entries)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return report
