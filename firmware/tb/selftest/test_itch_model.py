"""Simulator-free tests of the itch_decoder golden model (tb/model/itch.py, audit/12 §2.8).

The hand-written messages below are independent of encode() and of SPEC: their bytes and the
values they decode to were written by hand from the TotalView-ITCH 5.0 tables (one per type),
so the model and the encoder cannot share a mistake with them. Multi-byte values use distinct
bytes (01 02 .. 08) where they can, so a byte-order bug changes the value.
"""

import random
import re
from dataclasses import replace
from pathlib import Path

import pytest

from model.itch import (
    ALPHA,
    INT,
    LOCATE_ZERO,
    NORM,
    NORM_FIELDS,
    PRICE4,
    PRICE8,
    PRICE_MAX,
    SPEC,
    Bitmap,
    Event,
    ItchClass,
    ItchConfig,
    Outcome,
    Result,
    classify,
    decode,
    encode,
    expect,
    itch_categories,
    normalize,
    random_itch,
    wire,
)
from model.mold import ITCH50_LEN, MAX_MSG, MIN_LEN, FeedConfig, FeedState, random_feed
from model.mold import expect as mold_expect

HFT_PKG = Path(__file__).resolve().parents[2] / "rtl" / "core" / "hft_pkg.sv"


def hexbytes(text: str) -> bytes:
    """Hex bytes; whitespace and '#' comments are ignored."""
    return bytes.fromhex("".join(line.split("#")[0] for line in text.splitlines()).replace(" ", ""))


AAPL, MSFT, QQQ = b"AAPL    ", b"MSFT    ", b"QQQ     "
AAPL_HEX, MSFT_HEX, QQQ_HEX = "4141504c20202020", "4d53465420202020", "5151512020202020"
R12 = 0x0102030405060708  # distinct bytes: a reversed field reads 0x0807060504030201
R34 = 0x1112131415161718

# type: (hex, every field by hand). Header: type, locate, tracking, timestamp.
HAND: dict[str, tuple[str, dict]] = {
    "S": ("53 0000 0001 00000000002a 4f",
          {"locate": 0, "tracking": 1, "timestamp": 42, "event_code": b"O"}),
    "R": (f"""52 0001 0002 0000000003e8 {AAPL_HEX}
              51 4e 00000064 4e 43 5a20 50 4e 4e 31 4e 00000003 4e""",
          {"locate": 1, "tracking": 2, "timestamp": 1000, "stock": AAPL, "market_category": b"Q",
           "financial_status": b"N", "round_lot_size": 100, "round_lots_only": b"N", "issue_classification": b"C",
           "issue_subtype": b"Z ", "authenticity": b"P", "short_sale_threshold": b"N", "ipo_flag": b"N",
           "luld_tier": b"1", "etp_flag": b"N", "etp_leverage": 3, "inverse": b"N"}),
    "H": (f"48 0001 0003 000000000bb8 {AAPL_HEX} 48 20 54312020",
          {"locate": 1, "tracking": 3, "timestamp": 3000, "stock": AAPL, "trading_state": b"H", "reserved": b" ",
           "reason": b"T1  "}),
    "Y": (f"59 0002 0004 000000000001 {MSFT_HEX} 30",
          {"locate": 2, "tracking": 4, "timestamp": 1, "stock": MSFT, "reg_sho_action": b"0"}),
    "L": (f"4c 0002 0005 000000000002 4e534451 {MSFT_HEX} 59 4e 41",
          {"locate": 2, "tracking": 5, "timestamp": 2, "mpid": b"NSDQ", "stock": MSFT, "primary_mm": b"Y",
           "mm_mode": b"N", "mp_state": b"A"}),
    "V": ("56 0000 0006 000000000003 0102030405060708 1112131415161718 2122232425262728",
          {"locate": 0, "tracking": 6, "timestamp": 3, "level1": R12, "level2": R34, "level3": 0x2122232425262728}),
    "W": ("57 0000 0007 000000000004 31",
          {"locate": 0, "tracking": 7, "timestamp": 4, "breached_level": b"1"}),
    "K": ("4b 0000 0008 000000000005 5a565a5a54202020 00008ca0 41 000f4240",
          {"locate": 0, "tracking": 8, "timestamp": 5, "stock": b"ZVZZT   ", "release_time": 36000,
           "release_qualifier": b"A", "ipo_price": 1_000_000}),
    "J": ("4a 0003 0009 000000000006 5a565a5a54202020 000f4240 00112a88 000dbba0 00000002",
          {"locate": 3, "tracking": 9, "timestamp": 6, "stock": b"ZVZZT   ", "ref_price": 1_000_000,
           "upper_price": 1_125_000, "lower_price": 900_000, "extension": 2}),
    "h": ("68 0003 000a 000000000007 5a565a5a54202020 51 48",
          {"locate": 3, "tracking": 10, "timestamp": 7, "stock": b"ZVZZT   ", "market_code": b"Q",
           "halt_action": b"H"}),
    # K3: shares 00 00 25 1C = 9500, price 00 16 E3 60 = 1,500,000 = 150.0000.
    "A": (f"41 0004 000b 00000000000a 0000000000000001 42 0000251c {AAPL_HEX} 0016e360",
          {"locate": 4, "tracking": 11, "timestamp": 10, "order_ref": 1, "side": b"B", "shares": 9500,
           "stock": AAPL, "price": 1_500_000}),
    "F": (f"46 0004 000c 00000000000b 0102030405060708 53 000003e8 {AAPL_HEX} 77359400 4e534451",
          {"locate": 4, "tracking": 12, "timestamp": 11, "order_ref": R12, "side": b"S", "shares": 1000,
           "stock": AAPL, "price": PRICE_MAX, "attribution": b"NSDQ"}),
    "E": ("45 0004 000d 00000000000c 0000000000000001 00000064 0000000000000101",
          {"locate": 4, "tracking": 13, "timestamp": 12, "order_ref": 1, "executed_shares": 100, "match": 0x101}),
    "C": ("43 0004 000e 00000000000d 0102030405060708 000000c8 0000000000000102 59 0016e3c4",
          {"locate": 4, "tracking": 14, "timestamp": 13, "order_ref": R12, "executed_shares": 200, "match": 0x102,
           "printable": b"Y", "exec_price": 1_500_100}),
    "X": ("58 0004 000f 00000000000e 0000000000000001 00000032",
          {"locate": 4, "tracking": 15, "timestamp": 14, "order_ref": 1, "cancelled_shares": 50}),
    "D": ("44 0004 0010 00000000000f 0000000000000001",
          {"locate": 4, "tracking": 16, "timestamp": 15, "order_ref": 1}),
    "U": ("55 0004 0011 000000000010 0102030405060708 1112131415161718 00001388 0016e3c4",
          {"locate": 4, "tracking": 17, "timestamp": 16, "orig_ref": R12, "new_ref": R34, "shares": 5000,
           "price": 1_500_100}),
    "P": (f"50 0005 0012 000000000011 0000000000000000 42 00000190 {QQQ_HEX} 0055d4a8 0000000000000103",
          {"locate": 5, "tracking": 18, "timestamp": 17, "order_ref": 0, "side": b"B", "shares": 400, "stock": QQQ,
           "price": 5_625_000, "match": 0x103}),
    # Q: shares is 8 bytes; 2^32 does not fit in 4.
    "Q": (f"51 0005 0013 000000000012 0000000100000000 {QQQ_HEX} 0055d4a8 0000000000000104 4f",
          {"locate": 5, "tracking": 19, "timestamp": 18, "shares": 1 << 32, "stock": QQQ, "cross_price": 5_625_000,
           "match": 0x104, "cross_type": b"O"}),
    "B": ("42 0005 0014 000000000013 0000000000000103",
          {"locate": 5, "tracking": 20, "timestamp": 19, "match": 0x103}),
    "I": ("49 0006 0015 000000000014 00000000000186a0 0000000000002710 42 5350592020202020 00000000 0055d4a8 "
          "0055d4a8 43 4c",
          {"locate": 6, "tracking": 21, "timestamp": 20, "paired_shares": 100_000, "imbalance_shares": 10_000,
           "imbalance_direction": b"B", "stock": b"SPY     ", "far_price": 0, "near_price": 5_625_000,
           "ref_price": 5_625_000, "cross_type": b"C", "price_variation": b"L"}),
    "N": ("4e 0006 0016 000000000015 5350592020202020 41",
          {"locate": 6, "tracking": 22, "timestamp": 21, "stock": b"SPY     ", "interest_flag": b"A"}),
    "O": ("4f 0007 0017 000000000016 4e4557434f202020 59 000c3500 00249f00 00186a00 0000034630b8a000 0016e360 "
          "001b7740",
          {"locate": 7, "tracking": 23, "timestamp": 22, "stock": b"NEWCO   ", "open_eligibility": b"Y",
           "min_price": 800_000, "max_price": 2_400_000, "near_exec_price": 1_600_000,
           "near_exec_time": 3_600_000_000_000, "lower_collar": 1_500_000, "upper_collar": 1_800_000}),
}  # fmt: skip

# Normalized event fields of the decoded types, by hand (12 §2.4); the rest are 0.
HAND_NORM: dict[str, dict[str, int]] = {
    "A": {"order_ref": 1, "side": 0x42, "shares": 9500, "stock": 0x202020204C504141, "price": 1_500_000},
    "F": {"order_ref": R12, "side": 0x53, "shares": 1000, "stock": 0x202020204C504141, "price": PRICE_MAX,
          "attribution": 0x5144534E},
    "E": {"order_ref": 1, "shares": 100, "match": 0x101},
    "C": {"order_ref": R12, "shares": 200, "match": 0x102, "printable": 0x59, "price": 1_500_100},
    "X": {"order_ref": 1, "shares": 50},
    "D": {"order_ref": 1},
    "U": {"order_ref": R12, "new_ref": R34, "shares": 5000, "price": 1_500_100},
    "P": {"order_ref": 0, "side": 0x42, "shares": 400, "stock": 0x2020202020515151, "price": 5_625_000,
          "match": 0x103},
    "Q": {"shares": 1 << 32, "stock": 0x2020202020515151, "price": 5_625_000, "match": 0x104, "cross_type": 0x4F},
    "B": {"match": 0x103},
}  # fmt: skip

ALL = ItchConfig(class_en=0x3F, class_filter=0x00)
CLASSES = {
    ItchClass.ORDER: "AFECXDU",
    ItchClass.TRADE: "PQB",
    ItchClass.ADMIN: "SRHYLVWKJh",
    ItchClass.AUCTION: "INO",
}


def msg_of(typ: str) -> bytes:
    return hexbytes(HAND[typ][0])


def test_class_codes_match_rtl():
    """ItchClass must equal itch_class_e in hft_pkg.sv (the counter index of itch_stats)."""
    rtl = {m[1]: int(m[2]) for m in re.finditer(r"\bITCH_(\w+)\s*=\s*3'd(\d+)", HFT_PKG.read_text())}
    assert rtl == {c.name: c.value for c in ItchClass}


def test_spec_lengths_match_mold():
    """Two tables written out independently: SPEC here, ITCH50_LEN in the MoldUDP64 model."""
    assert {t: s.length for t, s in SPEC.items()} == ITCH50_LEN


def test_spec_classes():
    assert {c: "".join(t for t in SPEC if SPEC[t].cls == c) for c in CLASSES} == CLASSES
    assert set(NORM) == {t for t in SPEC if SPEC[t].cls in (ItchClass.ORDER, ItchClass.TRADE)}


@pytest.mark.parametrize("typ", list(SPEC))
def test_spec_fields_tile_the_message(typ):
    """Fields start at 0, follow each other without gap or overlap and end at the length;
    sizes fit their kind; names are unique."""
    spec = SPEC[typ]
    pos = 0
    for name, ofs, size, kind in spec.fields:
        assert ofs == pos, f"{typ}.{name} at {ofs}, previous field ends at {pos}"
        assert size == {PRICE4: 4, PRICE8: 8}.get(kind, size) and (kind != INT or size in (1, 2, 4, 6, 8))
        assert kind in (INT, ALPHA, PRICE4, PRICE8)
        pos += size
    assert pos == spec.length
    names = [f[0] for f in spec.fields]
    assert len(names) == len(set(names))
    assert set(NORM.get(typ, {}).values()) <= set(names)


@pytest.mark.parametrize("typ", list(HAND))
def test_hand_messages(typ):
    """Each hand-written message decodes to its hand-written values, encode() gives the same
    bytes back, and the event carries the hand-written normalized fields (0 elsewhere)."""
    msg, values = msg_of(typ), HAND[typ][1]
    assert len(msg) == SPEC[typ].length
    assert decode(msg) == {"type": typ.encode()} | values
    assert encode(typ, **values) == msg
    ev, res = expect(msg, ALL, Bitmap())
    assert res == Result(SPEC[typ].cls, Outcome.OUT)
    norm = HAND_NORM.get(typ, {})
    assert ev == Event(SPEC[typ].cls, ord(typ), len(msg), msg, values["locate"], values["tracking"],
                       values["timestamp"], **norm)  # fmt: skip
    assert normalize(msg) == norm


def test_wire_order():
    """Alpha fields in the event: byte 0 of the field in bits [7:0]."""
    assert wire(b"AB") == 0x4241
    assert wire(AAPL) == 0x202020204C504141


def test_k3_big_endian():
    """K3: shares 00 00 25 1C is 9500, not 0x1C250000."""
    msg = msg_of("A")
    assert msg[20:24] == bytes.fromhex("0000251c")
    assert decode(msg)["shares"] == 9500


def test_field_extremes():
    a = encode("A", timestamp=(1 << 48) - 1, order_ref=(1 << 64) - 1, shares=(1 << 32) - 1, price=PRICE_MAX)
    ev, _ = expect(a, ALL, Bitmap())
    assert (ev.timestamp, ev.order_ref, ev.shares, ev.price) == ((1 << 48) - 1, (1 << 64) - 1, (1 << 32) - 1, PRICE_MAX)
    q = encode("Q", shares=(1 << 64) - 1)
    assert expect(q, ALL, Bitmap())[0].shares == (1 << 64) - 1
    ones = bytes([ord("U")]) + b"\xff" * 34
    ev, _ = expect(ones, ALL, Bitmap())
    assert (ev.locate, ev.order_ref, ev.new_ref, ev.shares, ev.price) == (0xFFFF, (1 << 64) - 1, (1 << 64) - 1,
                                                                         (1 << 32) - 1, (1 << 32) - 1)  # fmt: skip
    with pytest.raises(OverflowError):
        encode("A", shares=1 << 32)


def test_all_type_bytes():
    """Every type byte at every length F3 can deliver: known types at their length decode,
    one byte off (or any other length) is MALFORMED, every other byte is UNKNOWN."""
    for b in range(256):
        for n in range(MIN_LEN, MAX_MSG + 1):
            spec = SPEC.get(chr(b))
            want = ItchClass.UNKNOWN if spec is None else spec.cls if n == spec.length else ItchClass.MALFORMED
            assert classify(b, n) == want, (b, n)
    assert sum(chr(b) in SPEC for b in range(256)) == 23


def test_header_rule():
    """User decision 2026-10-23: the common header is reported when the message is at least
    11 bytes, whatever the class (MALFORMED too); the filter key is the reported locate."""
    bm = Bitmap()
    cfg = ItchConfig(class_en=0x3F, class_filter=0x3F)  # every class through the filter, nothing subscribed
    for n in range(MIN_LEN, 11):  # 11 written by hand, not the model's HDR_LEN
        msg = bytes([0x00]) + b"\xab" * (n - 1)  # bytes 1-2 = 0xABAB, but too short for a header
        ev, res = expect(msg, cfg, bm)
        assert res == Result(ItchClass.UNKNOWN, Outcome.OUT), "locate 0: never filtered"
        assert (ev.locate, ev.tracking, ev.timestamp) == (0, 0, 0)
        d10 = msg_of("D")[:n]  # an Order Delete cut short: MALFORMED, no header
        assert expect(d10, cfg, bm) == (Event(ItchClass.MALFORMED, 0x44, n, d10), Result(ItchClass.MALFORMED, Outcome.OUT))
    unk = bytes([0x00]) + bytes.fromhex("abab 0102 030405060708")
    assert expect(unk, cfg, bm) == (None, Result(ItchClass.UNKNOWN, Outcome.FILTERED))
    ev, _ = expect(unk, ALL, bm)
    assert (ev.locate, ev.tracking, ev.timestamp) == (0xABAB, 0x0102, 0x030405060708)
    long_a = msg_of("A") + b"\x00"  # MALFORMED: header kept, no normalized fields
    assert expect(long_a, cfg, bm) == (None, Result(ItchClass.MALFORMED, Outcome.FILTERED))
    ev, _ = expect(long_a, ALL, bm)
    assert ev == Event(ItchClass.MALFORMED, 0x41, 37, long_a, 4, 11, 10)


@pytest.mark.parametrize("typ", list(SPEC))
def test_undecoded_classes_have_zero_fields(typ):
    """MALFORMED (one byte short or long) and ADMIN/AUCTION/UNKNOWN: every normalized field 0."""
    full = encode(typ, **{n: (1 << 8 * s) - 1 for n, _, s, k in SPEC[typ].body if k != ALPHA})
    for msg in (full[:-1], full + b"\x00") + (() if typ in NORM else (full,)):
        ev, _ = expect(msg, ALL, Bitmap())
        assert all(getattr(ev, f) == 0 for f in NORM_FIELDS), (typ, len(msg))


def test_pass_order():
    """MASKED before FILTERED before OUT; the filter applies only to classes in class_filter,
    never to locate 0, and reads bit (locate mod 32) of word (locate / 32)."""
    a = msg_of("A")  # ORDER, locate 4
    bm = Bitmap()
    order = 1 << ItchClass.ORDER
    assert expect(a, ItchConfig(0x3F & ~order, 0x3F), bm)[1].outcome == Outcome.MASKED
    assert expect(a, ItchConfig(0x3F & ~order, 0x00), bm)[1].outcome == Outcome.MASKED
    assert expect(a, ItchConfig(0x3F, order), bm)[1].outcome == Outcome.FILTERED
    assert expect(a, ItchConfig(0x3F, 0x3F & ~order), bm)[1].outcome == Outcome.OUT
    bm.set(4)
    assert expect(a, ItchConfig(0x3F, order), bm)[1].outcome == Outcome.OUT
    assert expect(a, ItchConfig(0x3F & ~order, order), bm)[1].outcome == Outcome.MASKED
    s = msg_of("S")  # locate 0
    assert expect(s, ItchConfig(0x3F, 0x3F), Bitmap())[1].outcome == Outcome.OUT


@pytest.mark.parametrize("locate", [1, 31, 32, 63, 64, 65535])
def test_bitmap_layout(locate):
    """Bit i of word w = locate 32w + i; neighbours stay unsubscribed."""
    bm = Bitmap()
    bm.write(locate // 32, 1 << locate % 32, 0xF)
    assert [bm.subscribed(x) for x in (locate - 1, locate, (locate + 1) % 65536)] == [False, True, False]
    msg = encode("A", locate=locate)
    cfg = ItchConfig(0x3F, 0x3F)
    assert expect(msg, cfg, bm)[1].outcome == Outcome.OUT
    other = locate - 1 if locate > 1 else 2  # locate 0 is never filtered
    assert expect(encode("A", locate=other), cfg, bm)[1].outcome == Outcome.FILTERED


def test_bitmap_write_strobes():
    bm = Bitmap()
    for strb in range(16):
        bm.write(7, 0, 0xF)
        bm.write(7, 0xA1B2C3D4, strb)
        assert bm.read(7) == sum((0xA1B2C3D4 >> 8 * b & 0xFF) << 8 * b for b in range(4) if strb >> b & 1)
    bm.write(7, 0xFFFFFFFF, 0xF)
    bm.write(7, 0, 0b0100)
    assert bm.read(7) == 0xFF00FFFF
    bm.set(7 * 32 + 31, False)
    assert bm.read(7) == 0x7F00FFFF


CFGS = {
    "all_out": ItchConfig(0x3F, 0x00),
    "filter_all": ItchConfig(0x3F, 0x3F),
    "mixed": ItchConfig(0b101011, 0b010101),
}


@pytest.mark.parametrize("seed", [1, 2])
@pytest.mark.parametrize("cfg_name", list(CFGS))
def test_random_itch_intent(seed, cfg_name):
    """2000 messages: the generator's intent agrees with expect() on every message, every
    category is hit, lengths stay within what F3 delivers, and every well-formed message
    decodes and re-encodes to itself (no field lost)."""
    cfg = CFGS[cfg_name]
    rng = random.Random(seed)
    bm = Bitmap()
    for loc in rng.sample(range(1, 65536), 3000) + [31, 65535]:
        bm.set(loc)
    msgs = random_itch(rng, 2000, cfg, bm)
    outcomes, classes = set(), set()
    for m in msgs:
        ev, res = expect(m.msg, cfg, bm)
        assert res == m.intent, f"{m.category}: generator meant {m.intent}, model says {res}"
        assert MIN_LEN <= len(m.msg) <= MAX_MSG
        assert (ev is None) == (res.outcome != Outcome.OUT)
        if res.cls not in (ItchClass.UNKNOWN, ItchClass.MALFORMED):
            values = {k: v for k, v in decode(m.msg).items() if k != "type"}
            assert encode(chr(m.msg[0]), **values) == m.msg
        outcomes.add(res.outcome)
        classes.add(res.cls)
    assert {m.category for m in msgs} == set(itch_categories())
    assert classes == set(ItchClass)
    want = {Outcome.OUT} | ({Outcome.FILTERED} if cfg.class_filter else set()) | (
        {Outcome.MASKED} if cfg.class_en != 0x3F else set())  # fmt: skip
    assert outcomes == want


def test_random_itch_edges():
    """The categories that carry the edge values do carry them."""
    rng, bm = random.Random(3), Bitmap()
    bm.set(100)
    msgs = random_itch(rng, 5 * len(itch_categories()), ALL, bm)
    by = {}
    for m in msgs:
        by.setdefault(m.category, []).append(m.msg)
    for loc in (0, 1, 31, 32, 65535):
        assert all(int.from_bytes(x[1:3], "big") == loc for x in by[f"locate_{loc}"])
    assert all(int.from_bytes(x[1:3], "big") == 100 for x in by["locate_subscribed"])
    assert all(int.from_bytes(x[5:11], "big") == (1 << 48) - 1 for x in by["extreme_ts_max"])
    assert all(x[1:] == bytes(len(x) - 1) for x in by["extreme_zeros"])
    assert all(x[1:] == b"\xff" * (len(x) - 1) for x in by["extreme_ones"])
    assert all(MIN_LEN <= len(x) < 11 for x in by["unknown_short"])
    assert {len(x) for x in by["unknown_header"]} == {11}
    for typ in SPEC:
        assert {len(x) for x in by[f"short_{typ}"]} == {SPEC[typ].length - 1}
        assert {len(x) for x in by[f"long_{typ}"]} == {SPEC[typ].length + 1}
        if typ in LOCATE_ZERO:
            assert all(x[1:3] == b"\x00\x00" for x in by[f"type_{typ}"])
    for x in by["extreme_price_max"]:
        prices = [v for n, _, _, k in SPEC[chr(x[0])].body if k == PRICE4 for v in [decode(x)[n]]]
        assert prices and all(v == PRICE_MAX for v in prices)
    for x in by["extreme_ref_max"]:
        assert decode(x)["orig_ref" if x[:1] == b"U" else "order_ref"] == (1 << 64) - 1


def test_mold_random_feed_with_itch_source():
    """random_feed() carries model.itch messages when given them as its source."""
    bm = Bitmap()
    bm.set(1)
    stream = iter(random_itch(random.Random(5), 100_000, ALL, bm))
    source = {}

    def nxt() -> bytes:
        m = next(stream)
        source[m.msg] = m
        return m.msg

    cfg = FeedConfig(True, True, b"SESSION001", 0x01)
    feed = random_feed(random.Random(4), 300, cfg, nxt)
    state, checked = FeedState(), 0
    for p in feed:
        res, state = mold_expect(p.payload, p.abort, p.filter_idx, replace(cfg, enable=p.enable), state)
        assert res.status == p.intent
        if p.category in ("sync", "ok", "dup_partial") or p.category.startswith(("gap_", "ok_filter_")):
            assert all(m in source for _, m in res.msgs), p.category  # every message from the source
            checked += len(res.msgs)
    assert checked > 0
