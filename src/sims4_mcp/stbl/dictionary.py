"""High-level STBL dictionary creation for the MCP tools."""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..crypto.fnv import fnv32
from .stbl import (LOCALE_ORDER, StblEntry, StblFile, build_stbl,
                   generate_locales, make_key)


def create_dictionary(strings: list[dict], base_name: str,
                      generate_all_locales: bool = True,
                      output_dir: str | None = None) -> dict:
    """Hash string references, build STBL resources and write them.

    ``strings`` items: ``{"key": "<reference>", "text": "...",
    "locale": "en_us"}``.  ``locale`` defaults to ``en_us``.

    Returns the key map (reference -> hex key) plus written file paths.
    """
    by_locale: dict[str, dict[str, str]] = {}
    key_map: dict[str, str] = {}
    for item in strings:
        ref = item.get("key") or item.get("reference") or item.get("name")
        text = item.get("text") or item.get("value") or ""
        locale = (item.get("locale") or "en_us").lower()
        if not ref:
            raise ValueError(f"string entry missing 'key': {item!r}")
        if locale not in LOCALE_ORDER:
            raise ValueError(
                f"unknown locale {locale!r}; choose from {LOCALE_ORDER}")
        by_locale.setdefault(locale, {})[ref] = text
        key_map[ref] = f"0x{make_key(ref):08X}"

    en_us = by_locale.setdefault("en_us", {})
    if not generate_all_locales:
        by_locale = {"en_us": en_us}

    out_root = (Path(output_dir).expanduser() if output_dir
                else config.cache_dir() / "stbl" / base_name)
    out_root.mkdir(parents=True, exist_ok=True)

    written: list[dict] = []
    if generate_all_locales:
        resources = generate_locales(base_name, by_locale)
    else:
        stbl = StblFile(entries=[
            StblEntry(key=make_key(ref), text=text)
            for ref, text in en_us.items()])
        instance = fnv32(base_name, high_bit=False)
        resources = [(instance, build_stbl(stbl))]

    for instance, blob in resources:
        path = out_root / f"{base_name}_{instance:016X}.stbl"
        path.write_bytes(blob)
        written.append({"instance": f"0x{instance:016X}",
                        "path": str(path), "size": len(blob)})

    return {"base_name": base_name, "keys": key_map,
            "locales": sorted(by_locale), "files": written,
            "output_dir": str(out_root)}
