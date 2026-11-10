"""STBL v5 round-trip and locale generation tests."""

from sims4_mcp.stbl.stbl import (LOCALE_ORDER, StblEntry, StblFile,
                                 build_stbl, generate_locales, make_key,
                                 parse_stbl, stbl_instance)


def test_roundtrip_entries():
    stbl = StblFile(language=1, entries=[
        StblEntry(key=make_key("mod:item.name"), text="Item Name"),
        StblEntry(key=make_key("mod:item.desc"),
                  text="Unicode: café — 日本語"),
    ])
    back = parse_stbl(build_stbl(stbl))
    assert back.language == 1
    assert back.as_dict() == stbl.as_dict()


def test_empty_table():
    back = parse_stbl(build_stbl(StblFile(entries=[])))
    assert back.entries == []


def test_keys_are_high_bit_fnv32():
    key = make_key("mod:item.name")
    assert key >> 31 == 1


def test_locale_instance_layout():
    en = stbl_instance("mymod", "en_us")
    de = stbl_instance("mymod", "de_de")
    assert en >> 56 == 0x00
    assert de >> 56 == 0x01
    assert en & 0xFFFFFFFF == de & 0xFFFFFFFF


def test_all_18_locales_generated():
    strings = {"en_us": {"mod:x": "X"},
               "de_de": {"mod:x": "X-de"}}
    resources = generate_locales("mymod", strings)
    assert len(resources) == 18
    by_high = {(inst >> 56): blob for inst, blob in resources}
    assert by_high[0x00] == resources[0][1]
    assert set(by_high) == set(range(0x12))
    # fallback: locales without translations use en_us text
    de = parse_stbl(by_high[0x01])
    ru = parse_stbl(by_high[0x11])
    assert de.entries[0].text == "X-de"
    assert ru.entries[0].text == "X"


def test_locale_order_complete():
    assert len(LOCALE_ORDER) == 18
