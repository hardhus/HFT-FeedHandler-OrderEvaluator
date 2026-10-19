"""Simulator-free tests of the moldudp64_rx golden model (tb/model/mold.py, audit/11 §2.8).

The hand-written packets below are independent of struct and of pack(): their session,
sequence numbers, counts and lengths were written by hand from the MoldUDP64 and ITCH 5.0
tables, so the model and the packet builder cannot share a mistake with them.
"""

import itertools
import random
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from model.mold import (
    ITCH50_LEN,
    SEQ_MOD,
    FeedConfig,
    FeedState,
    MoldResult,
    MoldStatus,
    eos,
    expect,
    heartbeat,
    itch_like,
    pack,
    random_feed,
    resync,
    seq_diff,
)

HFT_PKG = Path(__file__).resolve().parents[2] / "rtl" / "core" / "hft_pkg.sv"


def hexbytes(text: str) -> bytes:
    """Hex bytes; whitespace and '#' comments are ignored."""
    return bytes.fromhex("".join(line.split("#")[0] for line in text.splitlines()).replace(" ", ""))


SESSION = b"SESSION001"
MSG_S = hexbytes("53 0000 0000 000000000001 4f")  # System Event: locate, tracking, timestamp, 'O' (12 B)
MSG_D = hexbytes("44 0001 0000 000000000002 0000000000000007")  # Order Delete: ..., order ref 7 (19 B)
HDR_HEX = "53455353494f4e303031"  # "SESSION001"
PKT = hexbytes(f"""
    {HDR_HEX} 0000000000000064 0002  # session, seq 100, count 2
    000c 53 0000 0000 000000000001 4f
    0013 44 0001 0000 000000000002 0000000000000007
""")
HB = hexbytes(f"{HDR_HEX} 0000000000000066 0000")  # heartbeat, next seq 102
EOS = hexbytes(f"{HDR_HEX} 0000000000000066 ffff")  # end of session, next seq 102
PKT_UNSUP = hexbytes(f"""
    {HDR_HEX} 0000000000000064 0002
    000c 53 0000 0000 000000000001 4f
    0005 4142434445                  # 5-B message: legal MoldUDP64, not supported at line rate
""")
PKT_OVER = hexbytes(f"""
    {HDR_HEX} 0000000000000064 0003
    000c 53 0000 0000 000000000001 4f
    0041 {"00" * 65}                  # 65-B message (seq 101): skipped
    0013 44 0001 0000 000000000002 0000000000000007
""")
CFG = FeedConfig(enable=True, session_check=True, session=SESSION, filter_mask=0x01)
BOTH = ((100, MSG_S), (101, MSG_D))


def synced(e: int, eos_: bool = False) -> FeedState:
    return FeedState(e, True, eos_)


def test_status_codes_match_rtl():
    """MoldStatus must equal mold_status_e in hft_pkg.sv (the counter index of mold_stats)."""
    rtl = {m[1]: int(m[2]) for m in re.finditer(r"\bMOLD_(\w+)\s*=\s*4'd(\d+)", HFT_PKG.read_text())}
    assert rtl == {s.name: s.value for s in MoldStatus}


def test_builders_match_hand_packets():
    assert pack(SESSION, 100, [MSG_S, MSG_D]) == PKT
    assert heartbeat(SESSION, 102) == HB
    assert eos(SESSION, 102) == EOS
    assert pack(SESSION, 100, [MSG_S, bytes(65), MSG_D]) == PKT_OVER


S, R = MoldStatus, MoldResult
# name: (payload, abort, filter_idx, cfg, state, result, state after). Parametrized by name:
# conftest builds xdist group names from parameter reprs, which must match across workers.
KNOWN = {
    "sync_first": (PKT, False, 0, CFG, FeedState(), R(S.OK, BOTH, synced_now=True), synced(102)),
    "in_order": (PKT, False, 0, CFG, synced(100), R(S.OK, BOTH), synced(102)),
    "gap": (PKT, False, 0, CFG, synced(97), R(S.OK, BOTH, gap=3), synced(102)),
    "dup_full": (PKT, False, 0, CFG, synced(102), R(S.DUP, dup_msgs=2), synced(102)),
    "dup_old": (PKT, False, 0, CFG, synced(5000), R(S.DUP, dup_msgs=2), synced(5000)),
    "dup_partial": (PKT, False, 0, CFG, synced(101), R(S.OK, ((101, MSG_D),), dup_msgs=1), synced(102)),
    "eos_cleared": (PKT, False, 0, CFG, synced(100, True), R(S.OK, BOTH), synced(102)),
    "hb": (HB, False, 0, CFG, synced(102), R(S.HEARTBEAT), synced(102)),
    "hb_gap": (HB, False, 0, CFG, synced(100), R(S.HEARTBEAT, gap=2), synced(102)),
    "hb_old": (HB, False, 0, CFG, synced(103), R(S.DUP), synced(103)),
    "hb_keeps_eos": (HB, False, 0, CFG, synced(102, True), R(S.HEARTBEAT), synced(102, True)),
    "hb_sync": (HB, False, 0, CFG, FeedState(7), R(S.HEARTBEAT, synced_now=True), synced(102)),
    "eos": (EOS, False, 0, CFG, synced(102), R(S.EOS), synced(102, True)),
    "eos_old": (EOS, False, 0, CFG, synced(103), R(S.DUP), synced(103)),
    "disabled": (PKT, False, 0, replace(CFG, enable=False), synced(100), R(S.DISABLED), synced(100)),
    "other_feed": (PKT, False, 1, CFG, synced(100), R(S.OTHER_FEED), synced(100)),
    "b_line": (PKT, False, 5, replace(CFG, filter_mask=0x21), synced(100), R(S.OK, BOTH), synced(102)),
    "abort": (PKT, True, 0, CFG, synced(100), R(S.ABORT, BOTH), synced(100)),
    "abort_cut": (PKT[:40], True, 0, CFG, synced(100), R(S.ABORT, ((100, MSG_S),)), synced(100)),
    "abort_unsynced": (PKT, True, 0, CFG, FeedState(), R(S.ABORT, BOTH), FeedState()),
    "runt": (PKT[:19], False, 0, CFG, synced(100), R(S.RUNT), synced(100)),
    "session": (PKT, False, 0, replace(CFG, session=b"SESSION002"), synced(100), R(S.SESSION), synced(100)),
    "session_unchecked": (
        PKT, False, 0, replace(CFG, session=b"SESSION002", session_check=False), synced(100), R(S.OK, BOTH), synced(102)
    ),
    "unsupported": (PKT_UNSUP, False, 0, CFG, synced(100), R(S.UNSUPPORTED, ((100, MSG_S),)), synced(100)),
    "malformed_trailing": (PKT + b"\x00", False, 0, CFG, synced(100), R(S.MALFORMED, BOTH), synced(100)),
    "malformed_cut_msg": (PKT[:-1], False, 0, CFG, synced(100), R(S.MALFORMED, ((100, MSG_S),)), synced(100)),
    "malformed_cut_len": (PKT[:35], False, 0, CFG, synced(100), R(S.MALFORMED, ((100, MSG_S),)), synced(100)),
    "malformed_no_block": (PKT[:20], False, 0, CFG, synced(100), R(S.MALFORMED), synced(100)),
    "malformed_hb": (HB + b"\x00", False, 0, CFG, synced(102), R(S.MALFORMED), synced(102)),
    "oversize": (PKT_OVER, False, 0, CFG, synced(100), R(S.OK, ((100, MSG_S), (102, MSG_D)), oversize=1), synced(103)),
}  # fmt: skip


@pytest.mark.parametrize("name", list(KNOWN))
def test_hand_packets(name):
    payload, abort, idx, cfg, state, result, after = KNOWN[name]
    assert expect(payload, abort, idx, cfg, state) == (result, after)


def _add_block(p: bytes, block: bytes) -> bytes:
    """Append a block and count it."""
    count = int.from_bytes(p[18:20], "big") + 1
    return p[:18] + count.to_bytes(2, "big") + p[20:] + block


# One fault each, in the priority order of 11 §2.4: name -> (status, stage, apply). stage is
# the application order on the payload (session, extra block, trailing byte, then the cut).
Case = tuple[bytes, bool, int, FeedConfig, FeedState]
FAULTS: dict[str, tuple[MoldStatus, int, Callable[[Case], Case]]] = {
    "disabled": (S.DISABLED, 0, lambda c: (c[0], c[1], c[2], replace(c[3], enable=False), c[4])),
    "other_feed": (S.OTHER_FEED, 0, lambda c: (c[0], c[1], 1, c[3], c[4])),
    "abort": (S.ABORT, 0, lambda c: (c[0], True, c[2], c[3], c[4])),
    "runt": (S.RUNT, 3, lambda c: (c[0][:19], *c[1:])),
    "session": (S.SESSION, 0, lambda c: (b"SESSION999" + c[0][10:], *c[1:])),
    "unsupported": (S.UNSUPPORTED, 1, lambda c: (_add_block(c[0], b"\x00\x03abc"), *c[1:])),
    "malformed": (S.MALFORMED, 2, lambda c: (c[0] + b"\x00", *c[1:])),
    "dup": (S.DUP, 0, lambda c: (*c[:4], synced(200))),
}


@pytest.mark.parametrize("names", ["+".join(c) for k in (1, 2) for c in itertools.combinations(FAULTS, k)])
def test_priority_order(names):
    """Each fault alone gives its status; any two give the one listed first in 11 §2.4."""
    faults = [FAULTS[n] for n in names.split("+")]
    case: Case = (PKT, False, 0, CFG, synced(100))
    for _, _, apply in sorted(faults, key=lambda x: x[1]):
        case = apply(case)
    result, after = expect(*case)
    assert result.status == faults[0][0]
    assert after == case[4], "a rejected packet leaves the state alone"


def test_seq_wrap():
    """Serial number arithmetic across 2^64 (11 §2.4)."""
    assert seq_diff(1, SEQ_MOD - 1) == 2
    assert seq_diff(SEQ_MOD - 1, 1) == -2
    assert seq_diff(1 << 63, 0) == -(1 << 63), "half the space away counts as old"
    msgs = [itch_like(random.Random(0), "S") for _ in range(5)]
    top = SEQ_MOD - 3
    result, after = expect(pack(SESSION, top, msgs), False, 0, CFG, synced(top))
    assert [s for s, _ in result.msgs] == [top, top + 1, top + 2, 0, 1]
    assert after == synced(2)
    result, after = expect(pack(SESSION, 1, msgs), False, 0, CFG, synced(SEQ_MOD - 1))
    assert (result.status, result.gap, after) == (S.OK, 2, synced(6))
    result, _ = expect(pack(SESSION, SEQ_MOD - 1, msgs[:2]), False, 0, CFG, synced(1))
    assert (result.status, result.dup_msgs) == (S.DUP, 2), "S + C = E across the wrap"
    result, _ = expect(pack(SESSION, SEQ_MOD - 2, msgs), False, 0, CFG, synced(1))
    assert (result.status, [s for s, _ in result.msgs], result.dup_msgs) == (S.OK, [1, 2], 3)
    assert expect(heartbeat(SESSION, 0), False, 0, CFG, synced(SEQ_MOD - 1)) == (R(S.HEARTBEAT, gap=1), synced(0))


def test_resync():
    state = synced(500, True)
    assert resync(state, learn=False, seq=42) == synced(42)
    learned = resync(state, learn=True, seq=42)
    assert (learned.synced, learned.eos) == (False, False)
    result, after = expect(PKT, False, 0, CFG, learned)
    assert (result.status, result.synced_now, result.msgs, after) == (S.OK, True, BOTH, synced(102))


def test_itch_lengths():
    """The 23 ITCH 5.0 message types and their lengths (type byte included)."""
    assert sorted(ITCH50_LEN) == sorted("SRHYLVWKJhAFECXDUPQBINO")
    assert min(ITCH50_LEN.values()) == 12 and max(ITCH50_LEN.values()) == 50
    assert {t: ITCH50_LEN[t] for t in "AFECXDUS"} == {
        "A": 36, "F": 40, "E": 31, "C": 36, "X": 23, "D": 19, "U": 35, "S": 12
    }  # fmt: skip
    rng = random.Random(1)
    for typ, n in ITCH50_LEN.items():
        msg = itch_like(rng, typ)
        assert (len(msg), msg[:1]) == (n, typ.encode())


def _block_lanes(payload: bytes) -> list[tuple[int, int, int]]:
    """(start lane, end lane, length) of every block of a well-formed normal packet; lane =
    payload offset mod 8 (8-B beats, payload byte 0 in lane 0)."""
    out, pos = [], 20
    for _ in range(int.from_bytes(payload[18:20], "big")):
        n = int.from_bytes(payload[pos : pos + 2], "big")
        out.append((pos % 8, (pos + 1 + n) % 8, n))
        pos += 2 + n
    return out


@pytest.mark.parametrize("seed", [1, 2])
def test_random_feed_covers_categories(seed):
    """2000 packets: the generator's intent agrees with expect() on every packet (state carried
    from packet to packet), and every outcome, gap size and block alignment is hit."""
    rng = random.Random(seed)
    cfg = replace(CFG, filter_mask=0x21)  # A/B lines on filter entries 0 and 5
    feed = random_feed(rng, 2000, cfg)
    assert feed[0].category == "sync"
    state = FeedState()
    statuses, gaps, lanes, lengths, oversize, partial, synced_now = set(), set(), set(), set(), 0, 0, 0
    for p in feed:
        result, state = expect(p.payload, p.abort, p.filter_idx, replace(cfg, enable=p.enable), state)
        assert result.status == p.intent, f"{p.category}: generator meant {p.intent.name}, model says {result.status.name}"
        statuses.add(result.status)
        gaps.add(result.gap)
        synced_now += result.synced_now
        if result.status == S.OK:
            oversize += result.oversize
            partial += result.dup_msgs > 0
            for start, end, n in _block_lanes(p.payload):
                lanes.add((start, end))
                lengths.add(n)
    assert statuses == set(MoldStatus)
    assert {1, 1000, 1 << 40} <= gaps
    assert lanes == {(s, e) for s in range(8) for e in range(8)}
    assert set(ITCH50_LEN.values()) <= lengths and {65, 1000, 1400} <= lengths
    assert oversize > 0 and partial > 0
    assert synced_now == 1, "only the first packet synchronizes"
    assert {p.filter_idx for p in feed} == set(range(8)), "both lines of the feed and every other entry"
