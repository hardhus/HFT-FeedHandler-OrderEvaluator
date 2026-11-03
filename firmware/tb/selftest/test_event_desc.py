"""Simulator-free tests of the event descriptor schema and its generated outputs (audit/13 §2.4, §2.5).

The entries (desc_hand.py) and the layout table below are written by hand, independently of
event_desc.toml and scripts/gen_desc.py. event_desc_check.cpp checks the same layout against the C++
header, firmware/tb/sv/tb_event_desc.sv the same entries against the SV pack functions.
"""

import copy
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from desc_hand import HAND_EOS, HAND_GAP, HAND_ITCH, HAND_RAW

from model import desc_gen
from model.desc_gen import Kind

FW = Path(__file__).resolve().parents[2]
REPO = FW.parent
CPP_CHECK = Path(__file__).with_name("event_desc_check.cpp")

_spec = importlib.util.spec_from_file_location("gen_desc", REPO / "scripts" / "gen_desc.py")
gen_desc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_desc)

COMMON = {"seq": (0, 8), "sof_ts": (8, 8), "ring_idx": (16, 4), "kind": (20, 1), "flags": (21, 1), "cfg_epoch": (22, 1)}
CTRL = {"ctrl0": (63, 1), "ctrl1": (127, 1)}
LAYOUT = {
    "ITCH": {
        **COMMON, **CTRL, "itch_class": (23, 1), "msg_type": (24, 1), "len": (25, 1), "locate": (26, 2),
        "tracking": (28, 2), "itch_ts": (30, 6), "side": (36, 1), "printable": (37, 1), "cross_type": (38, 1),
        "price": (40, 4), "attribution": (44, 4), "order_ref": (48, 8), "new_ref": (64, 8), "shares": (72, 8),
        "match": (80, 8), "stock": (88, 8),
    },
    "ITCH_RAW": {**COMMON, **CTRL, "itch_class": (23, 1), "len": (24, 1), "raw_lo": (25, 38), "raw_hi": (64, 26)},
    "GAP": {**COMMON, **CTRL, "first_seq": (24, 8), "gap": (32, 8)},
    "EOS": {**COMMON, **CTRL},
}  # fmt: skip
CPP_STRUCT = {"ITCH": "ItchEntry", "ITCH_RAW": "ItchRawEntry", "GAP": "GapEntry", "EOS": "EosEntry"}

HEAD_VALUES = {"seq": 0x0102030405060708, "sof_ts": 0x1112131415161718, "ring_idx": 0x21222324}
ITCH_VALUES = {
    **HEAD_VALUES, "flags": 1, "cfg_epoch": 0x5A, "itch_class": 1, "msg_type": 0x50, "len": 44, "locate": 0x3132,
    "tracking": 0x3334, "itch_ts": 0x414243444546, "side": 0x42, "printable": 0x59, "cross_type": 0x4F,
    "price": 0x51525354, "attribution": b"ABCD", "order_ref": 0x6162636465666768, "new_ref": 0x7172737475767778,
    "shares": 0x8182838485868788, "match": 0x9192939495969798, "stock": b"AAPL    ", "ctrl0": 0x81, "ctrl1": 0x81,
}  # fmt: skip
RAW_VALUES = {
    **HEAD_VALUES, "cfg_epoch": 0xA5, "itch_class": 4, "len": 64, "raw_lo": bytes(range(38)),
    "raw_hi": bytes(range(38, 64)), "ctrl0": 0x81, "ctrl1": 0x81,
}  # fmt: skip
GAP_VALUES = {
    **HEAD_VALUES, "cfg_epoch": 7, "first_seq": 0xA1A2A3A4A5A6A7A8, "gap": 0xB1B2B3B4B5B6B7B8, "ctrl0": 1, "ctrl1": 1,
}  # fmt: skip
EOS_VALUES = {**HEAD_VALUES, "flags": 1, "cfg_epoch": 0xFF, "ctrl0": 0x81, "ctrl1": 0x81}
HAND = [
    (Kind.ITCH, ITCH_VALUES, HAND_ITCH),
    (Kind.ITCH_RAW, RAW_VALUES, HAND_RAW),
    (Kind.GAP, GAP_VALUES, HAND_GAP),
    (Kind.EOS, EOS_VALUES, HAND_EOS),
]


@pytest.mark.parametrize(("kind", "values", "entry"), HAND, ids=[k.name for k, _, _ in HAND])
def test_encode_gives_the_hand_written_entry(kind, values, entry):
    assert len(entry) == 128
    assert desc_gen.encode(kind, **values) == entry


@pytest.mark.parametrize(("kind", "values", "entry"), HAND, ids=[k.name for k, _, _ in HAND])
def test_decode_reads_the_hand_written_entry(kind, values, entry):
    out = desc_gen.decode(entry)
    assert out.pop("kind") == kind
    for name, value in out.items():
        assert value == values.get(name, 0), name


def test_field_tables_equal_the_hand_written_layout():
    got = {k.name: {n: (b, s) for n, (b, s, _) in desc_gen.FIELDS[k].items()} for k in Kind}
    assert got == LAYOUT


def test_constants_are_the_hand_written_ones():
    assert (desc_gen.VERSION, desc_gen.ENTRY_BYTES, desc_gen.LINE_BYTES) == (1, 128, 64)
    assert desc_gen.CTRL_BYTES == (63, 127)
    assert (desc_gen.CTRL_PHASE, desc_gen.CTRL_VERSION_MASK, desc_gen.FLAG_OVERFLOW_BEFORE) == (0x80, 0x0F, 0x01)
    assert {k.name: int(k) for k in Kind} == {"ITCH": 1, "ITCH_RAW": 2, "GAP": 3, "EOS": 4}


def test_encode_refuses_what_does_not_fit():
    with pytest.raises(KeyError):
        desc_gen.encode(Kind.EOS, gap=1)
    with pytest.raises(ValueError, match="stock"):
        desc_gen.encode(Kind.ITCH, stock=b"AAPL")
    with pytest.raises(OverflowError):
        desc_gen.encode(Kind.ITCH, itch_ts=1 << 48)
    with pytest.raises(ValueError, match="128 bytes"):
        desc_gen.decode(bytes(127))


# ---- The schema rules


def schema():
    return copy.deepcopy(gen_desc.load())


def itch_fields(s):
    return s["kind"][0]["fields"]


def set_field(target, **values):
    def change(s):
        next(f for f in itch_fields(s) if f["name"] == target).update(values)

    return change


def set_key(key, value):
    def change(s):
        s[key] = value

    return change


def set_kind(index, **values):
    def change(s):
        s["kind"][index].update(values)

    return change


def add_field(**f):
    def change(s):
        itch_fields(s).append({"size": 1, "type": "u8", "desc": "", **f})

    return change


def set_only(*kinds):
    def change(s):
        s["common"][-1]["only"] = list(kinds)

    return change


BROKEN = {
    "overlap": (set_field("side", byte=35), "overlaps"),
    "overlap_common": (add_field(name="x", byte=20), "overlaps"),
    "on_ctrl0": (add_field(name="x", byte=63), "overlaps ctrl"),
    "on_ctrl1": (add_field(name="x", byte=127), "overlaps ctrl"),
    "crosses_line": (add_field(name="x", byte=60, size=8, type="bytes"), "overlaps ctrl"),
    "outside": (add_field(name="x", byte=128), "outside"),
    "misaligned_u16": (set_field("locate", byte=27), "not aligned"),
    "misaligned_u32": (set_field("price", byte=41), "not aligned"),
    "misaligned_u48": (add_field(name="x", byte=57, size=6, type="u48"), "not aligned"),
    "misaligned_u64": (add_field(name="x", byte=100, size=8, type="u64"), "not aligned"),
    "wrong_size": (set_field("price", size=2), "is 4 bytes"),
    "unknown_type": (set_field("price", type="f32"), "unknown type"),
    "duplicate_name": (add_field(name="side", byte=39), "used twice"),
    "sv_keyword": (set_field("side", name="type"), "not a usable"),
    "python_keyword": (set_field("side", name="from"), "not a usable"),
    "upper_case": (set_field("side", name="Side"), "not a usable"),
    "kind_code_zero": (set_kind(0, code=0), "kind codes"),
    "kind_code_twice": (set_kind(0, code=2), "kind codes"),
    "kind_name_twice": (set_kind(0, name="GAP"), "share a name"),
    "version_zero": (set_key("version", 0), "version"),
    "version_too_big": (set_key("version", 16), "version"),
    "version_not_int": (set_key("version", True), "version"),
    "entry_not_two_lines": (set_key("entry_bytes", 256), "two lines"),
    "ctrl_not_last": (set_key("ctrl_bytes", [62, 127]), "last byte"),
    "phase_in_version_bits": (set_key("ctrl_phase_bit", 3), "above the version"),
    "flag_bit": (set_key("flags", {"a": 8}), "0..7"),
    "flags_share_a_bit": (set_key("flags", {"a": 1, "b": 1}), "share a bit"),
    "only_names_no_kind": (set_only("NOPE"), "names no kind"),
}


def test_the_schema_in_the_repository_passes():
    gen_desc.check_schema(schema())


@pytest.mark.parametrize("name", BROKEN)
def test_a_broken_schema_is_refused(name):
    change, message = BROKEN[name]
    s = schema()
    change(s)
    with pytest.raises(ValueError, match=message):
        gen_desc.check_schema(s)


def test_generator_writes_nothing_on_a_broken_schema(tmp_path, monkeypatch):
    bad = tmp_path / "s.toml"
    bad.write_text("version = 0\n", encoding="utf-8")
    monkeypatch.setattr(gen_desc, "SCHEMA", bad)
    with pytest.raises(SystemExit):
        gen_desc.main([str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


# ---- The C++ header


def test_cpp_header_compiles_with_hand_written_offsets():
    """software/include/gen/event_desc.h compiles as C++20 with no implicit padding, and its structs
    equal the hand-written offsets and sizes in event_desc_check.cpp (static_assert)."""
    gxx = shutil.which("g++")
    assert gxx, "g++ not found (run: just setup)"
    cmd = [gxx, "-std=c++20", "-Wall", "-Wextra", "-Wpadded", "-Werror", "-fsyntax-only"]
    cmd += [f"-I{REPO / 'software' / 'include'}", str(CPP_CHECK)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_cpp_check_covers_every_field():
    """event_desc_check.cpp names every field of LAYOUT, so a new field cannot skip it."""
    text = CPP_CHECK.read_text()
    for kind, fields in LAYOUT.items():
        for name, (byte, size) in fields.items():
            anchor = f"OFS({CPP_STRUCT[kind]}, {name}, {byte}, {size});"
            assert anchor in text, f"event_desc_check.cpp lacks {anchor}"
