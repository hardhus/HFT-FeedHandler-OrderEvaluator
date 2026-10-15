"""Simulator-free tests of the eth_ip_udp_rx golden model (tb/model/net.py, audit/09 §2.11).

The hand-written frames below are independent of scapy: their fields and IPv4 checksums
were written and summed by hand (RFC 1071), so the model and the frame builder cannot
share a mistake with them.
"""

import itertools
import random
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from model.net import (
    RxConfig,
    RxExpect,
    RxMeta,
    RxReason,
    expect,
    fix_ip_checksum,
    last_beat_payload_bytes,
    mcast_mac,
    pad_to,
    random_config,
    random_traffic,
    udp_frame,
    with_field,
)

HFT_PKG = Path(__file__).resolve().parents[2] / "rtl" / "core" / "hft_pkg.sv"


def hexframe(text: str) -> bytes:
    """Hex bytes; whitespace and '#' comments are ignored."""
    return bytes.fromhex("".join(line.split("#")[0] for line in text.splitlines()).replace(" ", ""))


def patch(frame: bytes, ofs: int, hex_bytes: str) -> bytes:
    raw = bytes.fromhex(hex_bytes)
    return frame[:ofs] + raw + frame[ofs + len(raw) :]


PAYLOAD = b"0123456789abcdefghij"
UCAST = hexframe("""
    020000000001 020000000099 0800                  # dst MAC, src MAC, EtherType IPv4
    4500 0030 0000 4000 4011 26bb 0a000001 0a000002 # IPv4: len 48, DF, TTL 64, UDP, csum, 10.0.0.1 -> 10.0.0.2
    04d2 1388 001c 0000                             # UDP: 1234 -> 5000, len 28, no checksum
    30313233343536373839616263646566 6768696a       # payload "0123456789abcdefghij"
""")
MCAST = hexframe("""
    01005e010203 020000000099 0800                  # dst = multicast MAC of 239.1.2.3
    4500 0030 0000 4000 4011 3fb8 0a000001 ef010203 # IPv4: 10.0.0.1 -> 239.1.2.3
    04d2 6720 001c 0000                             # UDP: 1234 -> 26400
    30313233343536373839616263646566 6768696a
""")
CFG = RxConfig(local_mac="02:00:00:00:00:01", filters=(("10.0.0.2", 5000, True), ("239.1.2.3", 26400, True)))
UCAST_META = RxMeta(0x0A000001, 0x0A000002, 5000, 20, 0)


def test_reason_codes_match_rtl():
    """RxReason must equal rx_reason_e in hft_pkg.sv (the counter index in F5)."""
    rtl = {m[1]: int(m[2]) for m in re.finditer(r"\bRX_(?:DROP_)?(\w+)\s*=\s*4'd(\d+)", HFT_PKG.read_text())}
    assert rtl == {r.name: r.value for r in RxReason}


def test_builder_matches_hand_frame():
    """The scapy-based builder produces exactly the hand-written bytes (no auto-fill surprises)."""
    assert udp_frame(PAYLOAD) == UCAST
    mc = udp_frame(PAYLOAD, dst_mac="01:00:5e:01:02:03", dst_ip="239.1.2.3", dport=26400)
    assert mc == MCAST


def drop(reason: RxReason, n: int = 62) -> RxExpect:
    return RxExpect(reason, n, None, False, None)


# name: (frame, tuser, cfg, expected). Parametrized by name: conftest builds xdist group
# names from parameter reprs, which must be the same in every worker process.
KNOWN = {
    "ucast": (UCAST, False, CFG, RxExpect(RxReason.OK, 62, PAYLOAD, False, UCAST_META)),
    "mcast": (MCAST, False, CFG, RxExpect(RxReason.OK, 62, PAYLOAD, False, RxMeta(0x0A000001, 0xEF010203, 26400, 20, 1))),
    "disabled": (UCAST, False, replace(CFG, rx_en=False), drop(RxReason.DISABLED)),
    "fcs": (UCAST, True, CFG, RxExpect(RxReason.FCS, 62, PAYLOAD, True, UCAST_META)),
    "hdr_short": (UCAST[:41], False, CFG, drop(RxReason.LEN, 41)),
    "mac": (patch(UCAST, 0, "020000000002"), False, CFG, drop(RxReason.MAC)),
    "mac_mcast_group": (patch(MCAST, 3, "010204"), False, CFG, drop(RxReason.MAC)),
    "arp": (patch(UCAST, 12, "0806"), False, CFG, drop(RxReason.ETHERTYPE)),
    "vlan": (patch(UCAST, 12, "8100"), False, CFG, drop(RxReason.ETHERTYPE)),
    "ip_csum": (patch(UCAST, 24, "26bc"), False, CFG, drop(RxReason.IP_HDR)),
    "ip_version": (patch(UCAST, 14, "65"), False, CFG, drop(RxReason.IP_HDR)),
    "frag_mf": (patch(patch(UCAST, 20, "2000"), 24, "46bb"), False, CFG, drop(RxReason.FRAG)),
    "frag_offset": (patch(patch(UCAST, 20, "0001"), 24, "66ba"), False, CFG, drop(RxReason.FRAG)),
    "proto_tcp": (patch(patch(UCAST, 23, "06"), 24, "26c6"), False, CFG, drop(RxReason.PROTO)),
    "filter_port": (patch(UCAST, 36, "1389"), False, CFG, drop(RxReason.FILTER)),
    "filter_disabled": (UCAST, False, replace(CFG, filters=(("10.0.0.2", 5000, False),)), drop(RxReason.FILTER)),
    "udp_len": (patch(UCAST, 38, "001d"), False, CFG, drop(RxReason.LEN)),
    "filter_lowest_enabled_idx": (
        UCAST, False, replace(CFG, filters=(("10.0.0.2", 5000, False),) + (("10.0.0.2", 5000, True),) * 2),
        RxExpect(RxReason.OK, 62, PAYLOAD, False, replace(UCAST_META, filter_idx=1)),
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", list(KNOWN))
def test_known_frames(name):
    frame, tuser, cfg, want = KNOWN[name]
    assert expect(frame, tuser, cfg) == want


def test_mac_rules():
    assert mcast_mac("239.1.2.3") == "01:00:5e:01:02:03"
    assert mcast_mac("239.129.2.3") == "01:00:5e:01:02:03", "bit 23 of the group address is not mapped"
    assert mcast_mac("224.0.0.251") == "01:00:5e:00:00:fb"
    # Another group in the same MAC (239.129.2.3): accepted at the MAC, decided by the IP filter.
    alias = fix_ip_checksum(patch(MCAST, 30, "ef810203"))
    assert expect(alias, False, CFG).reason == RxReason.FILTER
    bcast = patch(UCAST, 0, "ffffffffffff")
    assert expect(bcast, False, CFG).reason == RxReason.MAC
    assert expect(bcast, False, replace(CFG, accept_bcast=True)).reason == RxReason.OK
    # A multicast MAC is only accepted for a multicast dst IP.
    assert expect(patch(UCAST, 0, "01005e000002"), False, CFG).reason == RxReason.MAC


def test_ip_checksum_vector():
    """RFC 1071 §3 example sum, a known-good header, and a single flipped bit in each of
    the 10 header words."""
    from scapy.utils import checksum

    assert checksum(bytes.fromhex("0001f203f4f5f6f7")) == 0x220D
    assert checksum(bytes.fromhex("4500 0073 0000 4000 4011 b861 c0a8 0001 c0a8 00c7")) == 0
    for word in range(10):
        pos = 14 + 2 * word + word % 2  # alternate high and low byte
        bad = UCAST[:pos] + bytes([UCAST[pos] ^ 0x01]) + UCAST[pos + 1 :]
        assert expect(bad, False, CFG).reason == RxReason.IP_HDR, f"bit flip in header word {word}"


# One fault each, in the priority order of 09 §2.2: name -> (reason, stage, apply). stage is
# the application order, so that a later fault does not undo an earlier one (field edits,
# then the checksum, then lengths).
FAULTS: dict[str, tuple[RxReason, int, Callable]] = {
    "disabled": (RxReason.DISABLED, 0, lambda f, t, c: (f, t, replace(c, rx_en=False))),
    "fcs": (RxReason.FCS, 0, lambda f, t, c: (f, True, c)),
    "hdr_short": (RxReason.LEN, 3, lambda f, t, c: (f[:41], t, c)),
    "mac": (RxReason.MAC, 0, lambda f, t, c: (with_field(f, "dst_mac", "02:00:00:00:00:02"), t, c)),
    "ethertype": (RxReason.ETHERTYPE, 0, lambda f, t, c: (with_field(f, "ethertype", 0x86DD), t, c)),
    "ip_hdr": (RxReason.IP_HDR, 1, lambda f, t, c: (with_field(f, "ip_csum", int.from_bytes(f[24:26]) ^ 1), t, c)),
    "frag": (RxReason.FRAG, 0, lambda f, t, c: (with_field(f, "flags_frag", 0x2000), t, c)),
    "proto": (RxReason.PROTO, 0, lambda f, t, c: (with_field(f, "proto", 6), t, c)),
    "filter": (RxReason.FILTER, 0, lambda f, t, c: (with_field(f, "dport", 5001), t, c)),
    "udp_len": (RxReason.LEN, 0, lambda f, t, c: (with_field(f, "udp_len", 29), t, c)),
    "trailing": (RxReason.LEN, 2, lambda f, t, c: (f + b"\x00", t, c)),
}


@pytest.mark.parametrize("names", ["+".join(c) for k in (1, 2) for c in itertools.combinations(FAULTS, k)])
def test_priority_order(names):
    """Each fault alone gives its reason; any two give the one listed first in 09 §2.2."""
    faults = [FAULTS[n] for n in names.split("+")]
    frame, tuser, cfg = UCAST, False, CFG
    for _, _, apply in sorted(faults, key=lambda x: x[1]):
        frame, tuser, cfg = apply(frame, tuser, cfg)
    assert expect(frame, tuser, cfg).reason == faults[0][0]


def test_padding_rules():
    p4 = udp_frame(b"abcd")  # 46 B: 14 + 32
    assert expect(pad_to(p4), False, CFG) == RxExpect(RxReason.OK, 60, b"abcd", False, replace(UCAST_META, length=4))
    # Runt: the datagram is complete but the frame is below 60 B.
    runt = expect(pad_to(p4, 59), False, CFG)
    assert (runt.reason, runt.payload, runt.aborted) == (RxReason.LEN, b"abcd", True)
    # Padding beyond 60 B is not padding any more.
    assert expect(pad_to(p4, 64), False, CFG).reason == RxReason.LEN
    p18 = udp_frame(bytes(range(18)))  # exactly 60 B, no padding
    assert expect(p18, False, CFG).reason == RxReason.OK
    extra = expect(p18 + b"\x00", False, CFG)  # 61 B: one byte after the datagram
    assert (extra.reason, extra.payload, extra.aborted) == (RxReason.LEN, bytes(range(18)), True)
    # Truncated: the payload stops where the frame stops.
    p30 = udp_frame(bytes(range(30)))
    cut = expect(p30[:65], False, CFG)
    assert (cut.reason, cut.payload, cut.aborted) == (RxReason.LEN, bytes(range(23)), True)
    # Headers complete but not a single payload byte: no output packet.
    assert expect(p30[:42], False, CFG) == RxExpect(RxReason.LEN, 42, None, False, None)
    assert expect(p30[:42], True, CFG) == RxExpect(RxReason.FCS, 42, None, False, None)
    # udp_len = 8 (empty datagram, consistent lengths) is dropped in the header.
    empty = pad_to(udp_frame(b""))
    assert expect(empty, False, CFG) == RxExpect(RxReason.LEN, 60, None, False, None)
    # stat_bytes saturates.
    assert expect(bytes(70000), False, replace(CFG, rx_en=False)).nbytes == 0xFFFF


@pytest.mark.parametrize("seed", [1, 2])
def test_random_traffic_covers_categories(seed):
    """2000 frames: every category appears, the generator's intent agrees with expect() on
    every frame, and the coverage groups of 09 §2.12 that the stimulus decides are all hit."""
    rng = random.Random(seed)
    cfg = random_config(rng)
    traffic = random_traffic(rng, 2000, cfg)
    names = {t.category for t in traffic}
    tails = {f"ok_tail_{t}{s}" for t in range(1, 9) for s in ("", "_padded")} | {f"fcs_tail_{t}" for t in range(1, 9)}
    assert tails <= names
    assert {f"ok_filter_{i}" for i in range(8)} <= names

    reasons, tail_bins, mac_classes, idxs = set(), set(), set(), set()
    for t in traffic:
        got = expect(t.frame, t.tuser, replace(cfg, rx_en=t.rx_en))
        assert got.reason == t.intent, f"{t.category}: generator meant {t.intent.name}, model says {got.reason.name}"
        reasons.add(got.reason)
        if got.payload is None or got.reason == RxReason.LEN:
            continue
        kind = "abort" if got.aborted else "padded" if len(t.frame) == 60 and len(got.payload) < 18 else "nopad"
        tail_bins.add((last_beat_payload_bytes(len(got.payload)), kind))
        if got.reason == RxReason.OK:
            mac = t.frame[:6]
            mac_classes.add("bcast" if mac == b"\xff" * 6 else "mcast" if mac[0] & 1 else "ucast")
            idxs.add(got.meta.filter_idx)
    assert reasons == set(RxReason)
    assert tail_bins == {(n, k) for n in range(1, 9) for k in ("nopad", "padded", "abort")}
    assert mac_classes == {"ucast", "mcast", "bcast"}
    assert idxs == set(range(8))
