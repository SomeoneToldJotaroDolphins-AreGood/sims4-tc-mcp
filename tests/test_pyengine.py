"""Python 3.7 gatekeeper, pyc header, injector and packager tests."""

import json
import zipfile
from pathlib import Path

import pytest

from sims4_mcp.pyengine import packager
from sims4_mcp.pyengine.gatekeeper import check_source
from sims4_mcp.pyengine.injector import generate_hook_module
from sims4_mcp.pyengine.pyc import (MAGIC_3394, parse_pyc_header,
                                    pyc_header)

COMPATIBLE = [
    "x = 1\nprint(f'{x}')\n",
    "def f(a, b=1, *args, **kw):\n    return a\n",
    "from __future__ import annotations\ndef f(x: list[int]) -> None: pass\n",
    "items = [i for i in range(3)]\n",
]

INCOMPATIBLE = [
    ("walrus :=", "if (y := 5):\n    pass\n"),
    ("positional-only /", "def f(a, /, b):\n    pass\n"),
    ("match/case", "match x:\n    case 1:\n        pass\n"),
    ("dict | dict", 'd = {}\ne = d | {"a": 1}\n'),
    ("list[...]", "x = list[int]\n"),
    ("list[...] annotation", "def f(x: list[int]) -> None:\n    pass\n"),
    ("f'{x=}'", "x = 1\nprint(f'{x=}')\n"),
    ("X | Y annotation", "def f(x: int | None) -> None:\n    pass\n"),
]


@pytest.mark.parametrize("source", COMPATIBLE)
def test_compatible_sources(source):
    assert check_source(source).compatible


@pytest.mark.parametrize("construct,source", INCOMPATIBLE)
def test_incompatible_sources(construct, source):
    result = check_source(source)
    assert not result.compatible
    assert any(i.construct == construct for i in result.issues)


def test_syntax_error_is_reported():
    result = check_source("def broken(:\n")
    assert not result.compatible
    assert result.issues[0].construct == "SyntaxError"


def test_pyc_header_is_magic_3394():
    header = pyc_header(source_size=123, mtime=456)
    assert header[:4] == b"\x42\x0d\x0d\x0a"
    parsed = parse_pyc_header(header)
    assert parsed["magic"] == MAGIC_3394
    assert parsed["source_size"] == 123
    assert parsed["mtime"] == 456


def test_pyc_rejects_wrong_magic():
    with pytest.raises(ValueError):
        parse_pyc_header(b"\x42\x0d\x0d\x0b" + b"\x00" * 8)


@pytest.mark.parametrize("hook,cfg", [
    ("add_interaction", {"interaction_ids": [1, 2]}),
    ("remove_interaction", {"interaction_ids": ["0x123"]}),
    ("add_autonomy_commodity", {"commodities": ["discomfort"]}),
    ("start_buff", {"buff_ids": ["0x8000000000000001"], "duration": 60}),
    ("filter_super_affordances", {"allowed_ids": [5]}),
])
def test_generated_hooks_are_37_compatible(hook, cfg):
    source = generate_hook_module(hook, json.dumps(cfg))
    result = check_source(source)
    assert result.compatible, result.as_dict()["issues"]
    assert "def inject()" in source
    assert "def uninstall()" in source


def test_unknown_hook_rejected():
    with pytest.raises(ValueError):
        generate_hook_module("nope", "{}")


def test_missing_config_rejected():
    with pytest.raises(ValueError):
        generate_hook_module("add_interaction", "{}")


def test_packager_builds_archive(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "mod.py").write_text("def _on_load():\n    return 1\n")
    (src / "sub" / "util.py").write_text("X = 1\n")
    out = tmp_path / "out.ts4script"
    result = packager.build_ts4script(str(src), str(out),
                                      module_name="tcmod")
    assert result.compatible
    assert result.file_count == 3
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert names == {"mod.py", "sub/util.py", "mod_version.py"}
        version = zf.read("mod_version.py").decode()
        assert "minimum_script_version = 57" in version
        assert "package_name = 'tcmod'" in version


def test_packager_strict_blocks_incompatible(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text("if (x := 1):\n    pass\n")
    out = tmp_path / "out.ts4script"
    result = packager.build_ts4script(str(src), str(out), strict=True)
    assert not result.compatible
    assert not out.exists()
    assert result.errors


def test_packager_non_strict_still_writes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text("if (x := 1):\n    pass\n")
    out = tmp_path / "out.ts4script"
    result = packager.build_ts4script(str(src), str(out), strict=False)
    assert not result.compatible
    assert out.exists()


def test_packager_missing_source_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        packager.build_ts4script(str(tmp_path / "nope"),
                                str(tmp_path / "o.ts4script"))
