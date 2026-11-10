"""FNV-1 known-answer tests."""

from sims4_mcp.crypto.fnv import (fnv24, fnv32, fnv64, format_hash,
                                  hash_fnv)


def test_fnv32_canonical_vectors():
    # Standard FNV-1 test vectors (multiply-then-XOR).
    assert fnv32("", high_bit=False) == 0x811C9DC5
    assert fnv32("a", high_bit=False) == 0x050C5D7E
    assert fnv32("foobar", high_bit=False) == 0x31F0B262


def test_fnv64_canonical_vectors():
    assert fnv64("", high_bit=False) == 0xCBF29CE484222325
    assert fnv64("a", high_bit=False) == 0xAF63BD4C8601B7BE
    assert fnv64("foobar", high_bit=False) == 0x340D8765A4DDA9C2


def test_fnv24_canonical_vectors():
    assert fnv24("", high_bit=False) == 0x1C9DC5
    assert fnv24("a", high_bit=False) == 0x0C5D7E


def test_high_bit_sets_sign_bit():
    low = fnv32("sims4modding", high_bit=False)
    high = fnv32("sims4modding", high_bit=True)
    assert high == low | 0x80000000
    assert high >> 31 == 1

    low64 = fnv64("sims4modding", high_bit=False)
    high64 = fnv64("sims4modding", high_bit=True)
    assert high64 == low64 | 0x8000000000000000


def test_lowercase_normalization():
    assert fnv32("Sims4Modding", high_bit=False) == \
        fnv32("sims4modding", high_bit=False)
    assert fnv64("SIMS4", high_bit=True) == fnv64("sims4", high_bit=True)


def test_hash_fnv_dispatch_and_errors():
    assert hash_fnv("x", 32, False) == fnv32("x", False)
    assert hash_fnv("x", 64, True) == fnv64("x", True)
    try:
        hash_fnv("x", 128)  # type: ignore[arg-type]
    except ValueError as e:
        assert "Unsupported FNV width" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_format_hash_representations():
    r = format_hash("sims4modding", 64, True)
    d = r.as_dict()
    assert d["dec"] == str(r.decimal)
    assert d["hex"].startswith("0x") and len(d["hex"]) == 18
    assert len(d["tgi_instance"]) == 18
    assert int(d["hex"], 16) == r.decimal

    r32 = format_hash("sims4modding", 32, True)
    assert len(r32.hex) == 10          # 0x + 8 hex digits
    assert r32.decimal >> 31 == 1
