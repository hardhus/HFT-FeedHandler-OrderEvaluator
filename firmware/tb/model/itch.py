"""Golden model of itch_decoder (audit/12 §2.1-§2.5, §2.8). Pure Python, no simulator.

Decision: SPEC is the Nasdaq TotalView-ITCH 5.0 message table written out by hand (revision
2023-04-28; "p." = PDF page of the format table), and expect() reads a message as a byte
string through it. It never uses the RTL's vector layout or byte helpers, so a byte-order
(K3) or offset bug in the decoder cannot be mirrored here.

Protocol: every integer is big-endian and unsigned; alpha fields are ASCII, left-justified,
padded with spaces; Price(4)/Price(8) are integers with 4/8 implied decimals; timestamps are
nanoseconds since midnight. Every message starts with the common header type (1), stock
locate (2), tracking number (3), timestamp (6), so the header and the filter key are found
without knowing the type. Messages that are not stock dependent carry locate 0 (p. 3).

Rules (12 §2.3, user decision 2026-10-23 on the header):
- class: by the type byte; a known type whose length is not the specification length is
  MALFORMED; any other type byte is UNKNOWN.
- common header (locate, tracking, timestamp): reported when the message is at least 11 bytes
  long, whatever the class; shorter messages report all three as 0. The filter key is the
  reported locate, so a message shorter than 11 bytes is never filtered.
- pass: class disabled -> MASKED; class filtered, locate != 0 and its bitmap bit 0 ->
  FILTERED; else OUT. Field values are not validated.
- normalized fields (Event): only for ORDER and TRADE, by NORM; 0 elsewhere. Alpha fields go
  in wire order: byte 0 of the field in bits [7:0].

Generation: random_itch()/itch_stream() mix every category round-robin (each type, each type
one byte short and long, unknown type bytes, locate classes, field extremes), so coverage does
not depend on the seed; each message carries the class and outcome its category means (its
intent), computed from what the generator chose, not by calling expect().
"""

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, fields
from enum import IntEnum

from model.mold import MAX_MSG, MIN_LEN


class ItchClass(IntEnum):
    """Message class = itch_class_e in hft_pkg.sv (checked by test_class_codes_match_rtl)."""

    ORDER = 0
    TRADE = 1
    ADMIN = 2
    AUCTION = 3
    UNKNOWN = 4
    MALFORMED = 5


class Outcome(IntEnum):
    OUT = 0  # m_ev_valid
    FILTERED = 1  # class filtered and the locate is not subscribed
    MASKED = 2  # class disabled


DECODED = (ItchClass.ORDER, ItchClass.TRADE)

INT, ALPHA, PRICE4, PRICE8 = "Integer", "Alpha", "Price(4)", "Price(8)"
PRICE_MAX = 0x77359400  # 200,000.0000, the largest Price(4) (p. 4)
HDR_LEN = 11  # type, locate, tracking, timestamp
HEADER = (("type", 0, 1, ALPHA), ("locate", 1, 2, INT), ("tracking", 3, 2, INT), ("timestamp", 5, 6, INT))


@dataclass(frozen=True)
class MsgSpec:
    name: str
    length: int  # type byte included
    cls: ItchClass
    page: int  # PDF page of the format table
    body: tuple[tuple[str, int, int, str], ...]  # (name, offset, size, kind) after the header

    @property
    def fields(self) -> tuple[tuple[str, int, int, str], ...]:
        return HEADER + self.body


O, T, AD, AU = ItchClass.ORDER, ItchClass.TRADE, ItchClass.ADMIN, ItchClass.AUCTION
STOCK = ("stock", 11, 8, ALPHA)
SPEC: dict[str, MsgSpec] = {
    "S": MsgSpec("System Event", 12, AD, 4, (("event_code", 11, 1, ALPHA),)),  # locate always 0
    "R": MsgSpec("Stock Directory", 39, AD, 5, (
        STOCK, ("market_category", 19, 1, ALPHA), ("financial_status", 20, 1, ALPHA),
        ("round_lot_size", 21, 4, INT), ("round_lots_only", 25, 1, ALPHA),
        ("issue_classification", 26, 1, ALPHA), ("issue_subtype", 27, 2, ALPHA),
        ("authenticity", 29, 1, ALPHA), ("short_sale_threshold", 30, 1, ALPHA), ("ipo_flag", 31, 1, ALPHA),
        ("luld_tier", 32, 1, ALPHA), ("etp_flag", 33, 1, ALPHA), ("etp_leverage", 34, 4, INT),
        ("inverse", 38, 1, ALPHA),
    )),
    "H": MsgSpec("Stock Trading Action", 25, AD, 8, (
        STOCK, ("trading_state", 19, 1, ALPHA), ("reserved", 20, 1, ALPHA), ("reason", 21, 4, ALPHA),
    )),
    "Y": MsgSpec("Reg SHO Restriction", 20, AD, 9, (STOCK, ("reg_sho_action", 19, 1, ALPHA))),
    "L": MsgSpec("Market Participant Position", 26, AD, 9, (
        ("mpid", 11, 4, ALPHA), ("stock", 15, 8, ALPHA), ("primary_mm", 23, 1, ALPHA),
        ("mm_mode", 24, 1, ALPHA), ("mp_state", 25, 1, ALPHA),
    )),
    "V": MsgSpec("MWCB Decline Level", 35, AD, 10, (  # locate always 0
        ("level1", 11, 8, PRICE8), ("level2", 19, 8, PRICE8), ("level3", 27, 8, PRICE8),
    )),
    "W": MsgSpec("MWCB Status", 12, AD, 11, (("breached_level", 11, 1, ALPHA),)),  # locate always 0
    "K": MsgSpec("IPO Quoting Period Update", 28, AD, 11, (  # locate always 0
        STOCK, ("release_time", 19, 4, INT), ("release_qualifier", 23, 1, ALPHA), ("ipo_price", 24, 4, PRICE4),
    )),
    "J": MsgSpec("LULD Auction Collar", 35, AD, 12, (
        STOCK, ("ref_price", 19, 4, PRICE4), ("upper_price", 23, 4, PRICE4), ("lower_price", 27, 4, PRICE4),
        ("extension", 31, 4, INT),
    )),
    "h": MsgSpec("Operational Halt", 21, AD, 12, (
        STOCK, ("market_code", 19, 1, ALPHA), ("halt_action", 20, 1, ALPHA),
    )),
    "A": MsgSpec("Add Order", 36, O, 13, (
        ("order_ref", 11, 8, INT), ("side", 19, 1, ALPHA), ("shares", 20, 4, INT), ("stock", 24, 8, ALPHA),
        ("price", 32, 4, PRICE4),
    )),
    "F": MsgSpec("Add Order with MPID", 40, O, 13, (
        ("order_ref", 11, 8, INT), ("side", 19, 1, ALPHA), ("shares", 20, 4, INT), ("stock", 24, 8, ALPHA),
        ("price", 32, 4, PRICE4), ("attribution", 36, 4, ALPHA),
    )),
    "E": MsgSpec("Order Executed", 31, O, 14, (
        ("order_ref", 11, 8, INT), ("executed_shares", 19, 4, INT), ("match", 23, 8, INT),
    )),
    "C": MsgSpec("Order Executed with Price", 36, O, 15, (
        ("order_ref", 11, 8, INT), ("executed_shares", 19, 4, INT), ("match", 23, 8, INT),
        ("printable", 31, 1, ALPHA), ("exec_price", 32, 4, PRICE4),
    )),
    "X": MsgSpec("Order Cancel", 23, O, 15, (("order_ref", 11, 8, INT), ("cancelled_shares", 19, 4, INT))),
    "D": MsgSpec("Order Delete", 19, O, 15, (("order_ref", 11, 8, INT),)),
    "U": MsgSpec("Order Replace", 35, O, 16, (
        ("orig_ref", 11, 8, INT), ("new_ref", 19, 8, INT), ("shares", 27, 4, INT), ("price", 31, 4, PRICE4),
    )),
    "P": MsgSpec("Trade (non-cross)", 44, T, 16, (  # order_ref 0, side always 'B' (p. 16-17)
        ("order_ref", 11, 8, INT), ("side", 19, 1, ALPHA), ("shares", 20, 4, INT), ("stock", 24, 8, ALPHA),
        ("price", 32, 4, PRICE4), ("match", 36, 8, INT),
    )),
    "Q": MsgSpec("Cross Trade", 40, T, 17, (
        ("shares", 11, 8, INT), ("stock", 19, 8, ALPHA), ("cross_price", 27, 4, PRICE4), ("match", 31, 8, INT),
        ("cross_type", 39, 1, ALPHA),
    )),
    "B": MsgSpec("Broken Trade", 19, T, 18, (("match", 11, 8, INT),)),
    "I": MsgSpec("NOII", 50, AU, 18, (
        ("paired_shares", 11, 8, INT), ("imbalance_shares", 19, 8, INT), ("imbalance_direction", 27, 1, ALPHA),
        ("stock", 28, 8, ALPHA), ("far_price", 36, 4, PRICE4), ("near_price", 40, 4, PRICE4),
        ("ref_price", 44, 4, PRICE4), ("cross_type", 48, 1, ALPHA), ("price_variation", 49, 1, ALPHA),
    )),
    "N": MsgSpec("Retail Interest (RPII)", 20, AU, 19, (STOCK, ("interest_flag", 19, 1, ALPHA))),
    "O": MsgSpec("DLCR Price Discovery", 48, AU, 20, (
        STOCK, ("open_eligibility", 19, 1, ALPHA), ("min_price", 20, 4, PRICE4), ("max_price", 24, 4, PRICE4),
        ("near_exec_price", 28, 4, PRICE4), ("near_exec_time", 32, 8, INT), ("lower_collar", 40, 4, PRICE4),
        ("upper_collar", 44, 4, PRICE4),
    )),
}  # fmt: skip
LOCATE_ZERO = "SVWK"  # "Always 0" in the specification

# Normalized event field <- specification field, per decoded type (12 §2.4).
NORM: dict[str, dict[str, str]] = {
    "A": {"order_ref": "order_ref", "side": "side", "shares": "shares", "stock": "stock", "price": "price"},
    "F": {"order_ref": "order_ref", "side": "side", "shares": "shares", "stock": "stock", "price": "price",
          "attribution": "attribution"},
    "E": {"order_ref": "order_ref", "shares": "executed_shares", "match": "match"},
    "C": {"order_ref": "order_ref", "shares": "executed_shares", "match": "match", "printable": "printable",
          "price": "exec_price"},
    "X": {"order_ref": "order_ref", "shares": "cancelled_shares"},
    "D": {"order_ref": "order_ref"},
    "U": {"order_ref": "orig_ref", "new_ref": "new_ref", "shares": "shares", "price": "price"},
    "P": {"order_ref": "order_ref", "side": "side", "shares": "shares", "stock": "stock", "price": "price",
          "match": "match"},
    "Q": {"shares": "shares", "stock": "stock", "price": "cross_price", "match": "match", "cross_type": "cross_type"},
    "B": {"match": "match"},
}  # fmt: skip


@dataclass(frozen=True)
class Event:
    """m_ev_* of one OUT message (seq and sof_ts pass through and are the TB's business).
    Normalized fields that do not apply are 0; alpha fields are in wire order (wire())."""

    cls: ItchClass
    type: int
    length: int
    raw: bytes
    locate: int = 0
    tracking: int = 0
    timestamp: int = 0
    order_ref: int = 0
    new_ref: int = 0
    shares: int = 0
    price: int = 0
    match: int = 0
    side: int = 0
    stock: int = 0
    attribution: int = 0
    printable: int = 0
    cross_type: int = 0


NORM_FIELDS = tuple(f.name for f in fields(Event))[7:]  # order_ref .. cross_type


@dataclass(frozen=True)
class Result:
    """m_res_*: one per input message."""

    cls: ItchClass
    outcome: Outcome


@dataclass(frozen=True)
class ItchConfig:
    class_en: int = 0x3F  # bit c: class c passes (else MASKED)
    class_filter: int = 0x00  # bit c: class c goes through the symbol filter


N_WORDS = 2048  # 2048 x 32 bit = one bit per locate


class Bitmap:
    """Symbol filter RAM: bit i of word w = locate 32w + i (1 = subscribed). Reset value 0."""

    def __init__(self) -> None:
        self.words = [0] * N_WORDS

    def subscribed(self, locate: int) -> bool:
        return bool(self.words[locate >> 5] >> (locate & 31) & 1)

    def set(self, locate: int, on: bool = True) -> None:
        w, bit = locate >> 5, 1 << (locate & 31)
        self.words[w] = self.words[w] | bit if on else self.words[w] & ~bit

    def write(self, addr: int, data: int, strb: int) -> None:
        mask = sum(0xFF << 8 * b for b in range(4) if strb >> b & 1)
        self.words[addr] = self.words[addr] & ~mask | data & mask

    def read(self, addr: int) -> int:
        return self.words[addr]


def wire(b: bytes) -> int:
    """An alpha field as the RTL carries it: byte 0 in bits [7:0]."""
    return int.from_bytes(b, "little")


def classify(typ: int, length: int) -> ItchClass:
    spec = SPEC.get(chr(typ))
    if spec is None:
        return ItchClass.UNKNOWN
    return spec.cls if length == spec.length else ItchClass.MALFORMED


def header(msg: bytes) -> tuple[int, int, int]:
    """(locate, tracking, timestamp); all 0 for a message shorter than the header."""
    if len(msg) < HDR_LEN:
        return 0, 0, 0
    return int.from_bytes(msg[1:3], "big"), int.from_bytes(msg[3:5], "big"), int.from_bytes(msg[5:11], "big")


def decode(msg: bytes) -> dict[str, int | bytes]:
    """Every field of a well-formed message: integers as int, alpha fields as bytes."""
    spec = SPEC[chr(msg[0])]
    if len(msg) != spec.length:
        raise ValueError(f"{chr(msg[0])}: {len(msg)} bytes, the specification says {spec.length}")
    out: dict[str, int | bytes] = {}
    for name, ofs, size, kind in spec.fields:
        raw = msg[ofs : ofs + size]
        out[name] = raw if kind == ALPHA else int.from_bytes(raw, "big")
    return out


def encode(typ: str, **values: int | bytes | str) -> bytes:
    """A message of type typ; fields not given are 0 (integers) or spaces (alpha)."""
    spec = SPEC[typ]
    known = {name for name, *_ in spec.fields} - {"type"}
    if unknown := set(values) - known:
        raise KeyError(f"{typ}: no field {sorted(unknown)}")
    out = bytearray(spec.length)
    out[0] = ord(typ)
    for name, ofs, size, kind in spec.fields[1:]:
        v = values.get(name)
        if kind == ALPHA:
            b = b" " * size if v is None else v.encode() if isinstance(v, str) else bytes(v)
            if len(b) != size:
                raise ValueError(f"{typ}.{name}: {len(b)} bytes, field is {size}")
            out[ofs : ofs + size] = b
        else:
            out[ofs : ofs + size] = (v or 0).to_bytes(size, "big")  # OverflowError if it does not fit
    return bytes(out)


def normalize(msg: bytes) -> dict[str, int]:
    """Normalized event fields of a decoded (ORDER/TRADE) message; {} otherwise."""
    if classify(msg[0], len(msg)) not in DECODED:
        return {}
    values = decode(msg)
    return {ev: wire(v) if isinstance(v := values[sp], bytes) else v for ev, sp in NORM[chr(msg[0])].items()}


def expect(msg: bytes, cfg: ItchConfig, bitmap: Bitmap) -> tuple[Event | None, Result]:
    """Reference output of one message (12 §2.3, §2.4): the event if it is output, and the
    result every message gets."""
    cls = classify(msg[0], len(msg))
    locate, tracking, ts = header(msg)
    if not cfg.class_en >> cls & 1:
        outcome = Outcome.MASKED
    elif cfg.class_filter >> cls & 1 and locate != 0 and not bitmap.subscribed(locate):
        outcome = Outcome.FILTERED
    else:
        outcome = Outcome.OUT
    if outcome != Outcome.OUT:
        return None, Result(cls, outcome)
    ev = Event(cls, msg[0], len(msg), msg, locate, tracking, ts, **normalize(msg))
    return ev, Result(cls, outcome)


# ---------------------------------------------------------------------------------------
# Random messages

UNKNOWN_TYPES = (0x00, 0xFF, ord("T"))  # T: ITCH 4.1 seconds message, not in 5.0
EDGE_LOCATES = (0, 1, 31, 32, 65535)  # locate 0, first, word boundary, last


@dataclass(frozen=True)
class ItchMsg:
    msg: bytes
    category: str
    intent: Result  # what the generator meant (expect() must agree)


class _ItchGen:
    def __init__(self, rng: random.Random, cfg: ItchConfig, bitmap: Bitmap) -> None:
        self.rng, self.cfg, self.bitmap = rng, cfg, bitmap
        self.subs = [loc for loc in range(1, 1 << 16) if bitmap.subscribed(loc)]
        self.unsubs = [loc for loc in range(1, 1 << 16) if not bitmap.subscribed(loc)]
        self.subs_set = set(self.subs)
        if not self.subs or not self.unsubs:
            raise ValueError("random_itch needs a bitmap with subscribed and unsubscribed locates")

    def intent(self, cls: ItchClass, locate: int) -> Result:
        """Outcome from the generator's own choices (12 §2.3 order: mask, then filter)."""
        if not self.cfg.class_en >> cls & 1:
            return Result(cls, Outcome.MASKED)
        if self.cfg.class_filter >> cls & 1 and locate and locate not in self.subs_set:
            return Result(cls, Outcome.FILTERED)
        return Result(cls, Outcome.OUT)

    def locate(self, typ: str) -> int:
        if typ in LOCATE_ZERO:
            return 0
        r = self.rng.randrange(4)
        if r == 0:
            return self.rng.choice(self.subs)
        if r == 1:
            return self.rng.choice(self.unsubs)
        return self.rng.randrange(1 << 16) if r == 2 else 0

    def values(self, typ: str) -> dict[str, int | bytes]:
        rng, out = self.rng, {}
        for name, _, size, kind in SPEC[typ].fields[1:]:
            out[name] = rng.randbytes(size) if kind == ALPHA else rng.randrange(1 << 8 * size)
        return out

    def message(self, typ: str, locate: int | None = None, **override: int | bytes) -> bytes:
        v = self.values(typ)
        v["locate"] = self.locate(typ) if locate is None else locate
        v.update(override)
        return encode(typ, **v)

    # ---- categories: each returns (msg, class, locate the generator put in the header)
    def well_formed(self, typ: str, locate: int | None = None, **override: int | bytes):
        msg = self.message(typ, locate, **override)
        return msg, SPEC[typ].cls, int.from_bytes(msg[1:3], "big")

    def resized(self, typ: str, delta: int):
        msg = self.message(typ)
        msg = msg[:delta] if delta < 0 else msg + self.rng.randbytes(delta)
        return msg, ItchClass.MALFORMED, int.from_bytes(msg[1:3], "big")  # >= 11 bytes: header kept

    def unknown(self, typ: int | None, length: int | None = None):
        rng = self.rng
        if typ is None:
            typ = rng.choice([b for b in range(256) if chr(b) not in SPEC and b not in UNKNOWN_TYPES])
        n = rng.randint(MIN_LEN, MAX_MSG) if length is None else length
        msg = bytes([typ]) + rng.randbytes(n - 1)
        return msg, ItchClass.UNKNOWN, int.from_bytes(msg[1:3], "big") if n >= HDR_LEN else 0

    def extreme(self, kind: str):
        rng, typ = self.rng, self.rng.choice(sorted(SPEC))
        spec = SPEC[typ]
        if kind in ("zeros", "ones"):
            fill = 0 if kind == "zeros" else 0xFF
            msg = bytes([ord(typ)]) + bytes([fill]) * (spec.length - 1)
            return msg, spec.cls, int.from_bytes(msg[1:3], "big")
        if kind == "price_max":
            typ = rng.choice([t for t in SPEC if any(k == PRICE4 for *_, k in SPEC[t].body)])
            prices = {n: PRICE_MAX for n, _, _, k in SPEC[typ].body if k == PRICE4}
            return self.well_formed(typ, **prices)
        if kind == "ts_max":
            return self.well_formed(typ, timestamp=(1 << 48) - 1)
        typ = rng.choice([t for t in SPEC if any(n == "order_ref" or n == "orig_ref" for n, *_ in SPEC[t].body)])
        ref = "orig_ref" if typ == "U" else "order_ref"
        return self.well_formed(typ, **{ref: (1 << 64) - 1})

    def categories(self) -> list[tuple[str, Callable[[], tuple[bytes, ItchClass, int]]]]:
        cats: list[tuple[str, Callable[[], tuple[bytes, ItchClass, int]]]] = []
        cats += [(f"type_{t}", lambda t=t: self.well_formed(t)) for t in SPEC]
        cats += [(f"short_{t}", lambda t=t: self.resized(t, -1)) for t in SPEC]
        cats += [(f"long_{t}", lambda t=t: self.resized(t, 1)) for t in SPEC]
        cats += [(f"unknown_{b:02x}", lambda b=b: self.unknown(b)) for b in UNKNOWN_TYPES]
        cats += [("unknown_other", lambda: self.unknown(None))]
        cats += [("unknown_short", lambda: self.unknown(None, self.rng.randint(MIN_LEN, HDR_LEN - 1)))]
        cats += [("unknown_header", lambda: self.unknown(None, HDR_LEN))]
        stocks = [t for t in SPEC if t not in LOCATE_ZERO]
        cats += [(f"locate_{loc}", lambda loc=loc: self.well_formed(self.rng.choice(stocks), loc)) for loc in EDGE_LOCATES]
        cats += [
            ("locate_subscribed", lambda: self.well_formed(self.rng.choice(stocks), self.rng.choice(self.subs))),
            ("locate_unsubscribed", lambda: self.well_formed(self.rng.choice(stocks), self.rng.choice(self.unsubs))),
        ]  # fmt: skip
        cats += [(f"extreme_{k}", lambda k=k: self.extreme(k)) for k in ("zeros", "ones", "price_max", "ts_max", "ref_max")]
        return cats


def itch_categories() -> list[str]:
    """Category names of the generator: coverage bins."""
    bm = Bitmap()
    bm.set(1)
    return [name for name, _ in _ItchGen(random.Random(0), ItchConfig(), bm).categories()]


def itch_stream(rng: random.Random, cfg: ItchConfig, bitmap: Bitmap) -> Iterator[ItchMsg]:
    """Endless messages: each round holds every category once, in random order. The intent
    uses cfg and the bitmap as they are now; a test that changes them later checks with
    expect() instead."""
    gen = _ItchGen(rng, cfg, bitmap)
    cats = gen.categories()
    while True:
        rnd = cats[:]
        rng.shuffle(rnd)
        for name, make in rnd:
            msg, cls, locate = make()
            yield ItchMsg(msg, name, gen.intent(cls, locate))


def random_itch(rng: random.Random, n: int, cfg: ItchConfig, bitmap: Bitmap) -> list[ItchMsg]:
    """n messages; every category appears once n covers one round, whatever the seed."""
    stream = itch_stream(rng, cfg, bitmap)
    return [next(stream) for _ in range(n)]
