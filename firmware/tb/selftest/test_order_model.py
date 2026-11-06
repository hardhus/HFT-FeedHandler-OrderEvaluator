"""Simulator-free tests of the order model (tb/model/order.py) and the simulated exchange
(tb/common/exchange.py); audit/14 §2.6, §2.9, decisions 9-13.

The expected bytes are the worked example of docs/protocol/order_msg.md, written here by hand from
the OUCH 5.0 table, not through encode_ouch(). The header is checked with scapy.
"""

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from common.exchange import Exchange, ExchangeError
from model.order import (
    FRAME_LEN,
    PRICE_LIMIT_MAX,
    TX_QUEUE_DEPTH,
    USER_REF_MAX,
    Ouch,
    OrderError,
    OrderReq,
    Source,
    TxConfig,
    TxPort,
    decode_ouch,
    encode_ouch,
    frame,
    hw_clordid,
    soupbin_u,
    template,
    unwrap_soupbin,
    valid_order,
)

# docs/protocol/order_msg.md, "Worked example": frame bytes 42..91.
DOC_EXAMPLE = bytes.fromhex(
    "00 30 55 4f 00 00 00 01 42 00 00 00 64 41 41 50"
    "4c 20 20 20 20 00 00 00 00 00 16 ed 24 30 59 50"
    "4e 4e 48 57 30 30 30 30 30 30 30 30 31 20 20 20"
    "00 00"
)
DOC_OUCH = Ouch(
    user_ref=1, side=ord("B"), qty=100, stock=b"AAPL    ", price=1502500, tif=ord("0"), display=ord("Y"),
    capacity=ord("P"), ise=ord("N"), cross_type=ord("N"), clordid=b"HW000000001   ",
)  # fmt: skip
# Distinct bytes in every multi-byte field, so a byte-order or offset bug changes the message.
ODD_OUCH = Ouch(
    user_ref=0x01020304, side=ord("S"), qty=0x0A0B0C0D, stock=b"ZVZZT   ", price=0x1112131415161718, tif=ord("E"),
    display=ord("N"), capacity=ord("A"), ise=ord("Y"), cross_type=ord("C"), clordid=b"abcdefghijklmn",
)  # fmt: skip
ODD_HEX = (
    "4f 01020304 53 0a0b0c0d 5a565a5a54202020 1112131415161718 45 4e 41 59 43"
    "6162636465666768696a6b6c6d6e 0000"
)
CFG = TxConfig(template())


def host_req(**kw):
    base = {
        "source": Source.HOST, "side": ord("B"), "qty": 100, "price": 1502500, "locate": 7, "stock": b"AAPL    ",
        "tif": ord("0"), "clordid": b"CLIENT 0001   ",
    }  # fmt: skip
    return OrderReq(**{**base, **kw})


def hw_req(**kw):
    return host_req(source=Source.RULE, rule=3, clordid=None, **kw)


# ---- Encoding


def test_the_worked_example_of_the_doc():
    assert soupbin_u(encode_ouch(DOC_OUCH)) == DOC_EXAMPLE
    assert decode_ouch(unwrap_soupbin(DOC_EXAMPLE)) == DOC_OUCH


def test_every_field_lands_on_its_offset():
    assert encode_ouch(ODD_OUCH) == bytes.fromhex(ODD_HEX)
    assert decode_ouch(bytes.fromhex(ODD_HEX)) == ODD_OUCH


def test_the_frame_is_the_template_the_envelope_and_the_message():
    f = frame(CFG, DOC_OUCH)
    assert len(f) == FRAME_LEN == 92
    assert f[:42] == CFG.template and f[42:] == DOC_EXAMPLE


def test_the_template_header_fields():
    eth = Ether(template(src_ip="192.168.1.10", dst_ip="10.1.2.3", sport=40000, dport=15000) + bytes(50))
    ip, udp = eth[IP], eth[UDP]
    assert (ip.len, ip.id, ip.flags.value, ip.frag, ip.proto, ip.ihl) == (78, 0, 2, 0, 17, 5)
    assert (udp.len, udp.chksum, udp.sport, udp.dport) == (58, 0, 40000, 15000)
    sent = ip.chksum
    del ip.chksum
    assert IP(bytes(ip)).chksum == sent


@pytest.mark.parametrize(
    ("rule", "ref", "want"),
    [(0, 1, b"HW000000001   "), (7, 0xFFFFFFFF, b"HW7FFFFFFFF   "), (5, 0x00ABCDEF, b"HW500ABCDEF   ")],
)
def test_hardware_clordid(rule, ref, want):
    assert hw_clordid(rule, ref) == want


BAD_MESSAGES = {
    "short": (DOC_EXAMPLE[3:-1], "47 bytes"),
    "type": (b"U" + DOC_EXAMPLE[4:], "message type"),
    "appendage": (DOC_EXAMPLE[3:-1] + b"\x01", "Appendage Length"),
}


@pytest.mark.parametrize("name", BAD_MESSAGES)
def test_decode_refuses_a_bad_message(name):
    msg, match = BAD_MESSAGES[name]
    with pytest.raises(OrderError, match=match):
        decode_ouch(msg)


@pytest.mark.parametrize(
    ("pkt", "match"),
    [(b"\x00\x31" + DOC_EXAMPLE[2:], "Packet Length"), (DOC_EXAMPLE[:2] + b"S" + DOC_EXAMPLE[3:], "type")],
)
def test_unwrap_refuses_a_bad_envelope(pkt, match):
    with pytest.raises(OrderError, match=match):
        unwrap_soupbin(pkt)


# ---- Validity (decisions 9, 10, 13)

VALID_CASES = [
    ("price_1", {"price": 1}, True),
    ("price_max", {"price": PRICE_LIMIT_MAX}, True),
    ("price_0", {"price": 0}, False),
    ("price_max_plus_1", {"price": PRICE_LIMIT_MAX + 1}, False),
    ("price_market", {"price": 0x7FFFFFFF}, False),
    ("price_negative", {"price": -1}, False),
    ("price_33_bits", {"price": 1 << 32}, False),
    ("qty_1", {"qty": 1}, True),
    ("qty_999999", {"qty": 999_999}, True),
    ("qty_0", {"qty": 0}, False),
    ("qty_1000000", {"qty": 1_000_000}, False),
    *[(f"side_{c}", {"side": ord(c)}, c in "BS") for c in "BSTE"],
    ("side_0", {"side": 0}, False),
    *[(f"tif_{c}", {"tif": ord(c)}, c in "035E") for c in "0356E1"],
    ("clordid_symbol", {"clordid": b"CLIENT-0001   "}, False),
    ("clordid_nul", {"clordid": b"CLIENT\x000001   "}, False),
    ("clordid_short", {"clordid": b"CLIENT"}, False),
    ("clordid_none", {"clordid": None}, False),
    ("clordid_spaces", {"clordid": b" " * 14}, True),
]


@pytest.mark.parametrize(("name", "change", "want"), VALID_CASES, ids=[c[0] for c in VALID_CASES])
def test_valid_order(name, change, want):
    assert valid_order(host_req(**change)) is want


def test_a_hardware_order_needs_no_clordid():
    assert valid_order(hw_req())
    assert not valid_order(hw_req(price=0))


# ---- TX port


def test_tx_port_numbers_orders_in_order():
    tx = TxPort(CFG)
    for i in range(3):
        tx.accept(hw_req(qty=i + 1) if i % 2 else host_req(qty=i + 1))
    sent = [tx.send() for _ in range(3)]
    assert tx.send() is None
    assert [(s.user_ref, s.req.qty) for s in sent] == [(1, 1), (2, 2), (3, 3)]
    assert decode_ouch(unwrap_soupbin(sent[1].frame[42:])).clordid == b"HW300000002   "
    assert decode_ouch(unwrap_soupbin(sent[2].frame[42:])).clordid == b"CLIENT 0001   "


def test_tx_queue_holds_eight():
    tx = TxPort(CFG)
    for _ in range(TX_QUEUE_DEPTH):
        assert tx.free()
        tx.accept(host_req())
    assert not tx.free()
    tx.send()
    assert tx.free()


def test_user_ref_counter_stops_at_the_last_number():
    tx = TxPort(CFG)
    tx.write_user_ref_next(USER_REF_MAX - 1)
    assert tx.refs_left() == 2
    tx.accept(host_req())
    assert tx.refs_left() == 1
    tx.accept(host_req())
    assert tx.refs_left() == 0
    assert [tx.send().user_ref, tx.send().user_ref] == [USER_REF_MAX - 1, USER_REF_MAX]
    assert tx.refs_left() == 0


def test_tx_off_empties_the_queue_in_order():
    tx = TxPort(CFG)
    reqs = [host_req(qty=q) for q in (5, 6, 7)]
    for r in reqs:
        tx.accept(r)
    assert tx.tx_off() == reqs
    assert tx.send() is None and tx.user_ref_next == 1


# ---- Exchange


def sent_frames(n, start=1):
    tx = TxPort(CFG, user_ref_next=start)
    out = []
    for i in range(n):
        tx.accept(hw_req(qty=i + 1))
        out.append(tx.send().frame)
    return out


def test_exchange_takes_the_core_frames():
    ex = Exchange(CFG.template, user_ref_next=10)
    got = [ex.receive(f) for f in sent_frames(3, start=10)]
    assert [(o.user_ref, o.qty) for o in got] == [(10, 1), (11, 2), (12, 3)]


def test_exchange_reads_the_doc_example():
    o = Exchange(CFG.template).receive(CFG.template + DOC_EXAMPLE)
    assert o == DOC_OUCH


def corrupt(f, ofs, value):
    return f[:ofs] + bytes([value]) + f[ofs + 1 :]


GOOD = frame(CFG, DOC_OUCH)
BAD_FRAMES = {
    "short": (GOOD[:-1], "91 bytes"),
    "header_byte": (corrupt(GOOD, 30, GOOD[30] ^ 1), "template"),
    "soup_length": (corrupt(GOOD, 43, 0x31), "Packet Length"),
    "soup_type": (corrupt(GOOD, 44, ord("S")), "packet type"),
    "ouch_type": (corrupt(GOOD, 45, ord("U")), "message type"),
    "user_ref_skipped": (corrupt(GOOD, 49, 2), "UserRefNum 2"),
    "side_short": (corrupt(GOOD, 50, ord("T")), "side"),
    "tif_gtt": (corrupt(GOOD, 71, ord("6")), "tif"),
    "qty_0": (GOOD[:51] + bytes(4) + GOOD[55:], "qty"),
    "price_market": (GOOD[:63] + (0x7FFFFFFF).to_bytes(8, "big") + GOOD[71:], "price"),
    "clordid": (corrupt(GOOD, 80, ord("-")), "clordid"),
    "appendage": (corrupt(GOOD, 91, 1), "Appendage"),
}


@pytest.mark.parametrize("name", BAD_FRAMES)
def test_exchange_refuses_a_bad_frame(name):
    f, match = BAD_FRAMES[name]
    with pytest.raises(ExchangeError, match=match):
        Exchange(CFG.template).receive(f)


def test_exchange_refuses_a_wrong_ip_checksum_in_the_template():
    bad = corrupt(CFG.template, 25, CFG.template[25] ^ 0x10)
    with pytest.raises(ExchangeError, match="checksum"):
        Exchange(bad).receive(bad + DOC_EXAMPLE)


def test_exchange_refuses_a_repeated_user_ref():
    ex = Exchange(CFG.template)
    ex.receive(GOOD)
    with pytest.raises(ExchangeError, match="UserRefNum 1, want 2"):
        ex.receive(GOOD)


def test_user_ref_next_is_written_only_while_killed_with_an_empty_queue():
    """Decision 22."""
    tx = TxPort(CFG)
    assert not tx.write_user_ref_next(50, killed=False) and tx.user_ref_next == 1
    tx.accept(host_req())
    assert not tx.write_user_ref_next(50, killed=True) and tx.user_ref_next == 1
    tx.tx_off()
    assert tx.write_user_ref_next(50, killed=True) and tx.user_ref_next == 50
    assert tx.write_user_ref_next(3, killed=True) and tx.user_ref_next == 3  # may go down while killed
