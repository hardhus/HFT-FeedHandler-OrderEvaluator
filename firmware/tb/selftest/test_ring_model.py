"""Simulator-free tests of the ring model (tb/model/ring.py, audit/13 §2.2, §2.7).

The expected values are stated here field by field, or byte by byte for one full entry
(desc_hand.py), not through message_entry(), so a wrong field mapping in the model fails.
"""

import random
from pathlib import Path

import pytest
from desc_hand import HAND_ITCH, HAND_RAW

from model import desc_gen
from model.desc_gen import FLAG_OVERFLOW_BEFORE, Kind
from model.itch import Bitmap, Event, ItchClass, ItchConfig, Outcome, encode, expect, random_itch, wire
from model.mold import ACCEPTED, MoldStatus
from model.ring import (
    MAX_PKT_ENTRIES,
    PACINGS,
    Pacing,
    RingConsumer,
    RingError,
    RingMemory,
    RingPacket,
    expect_entries,
    phase_of,
    slot_addr,
    stamp,
)

REJECTED = [s for s in MoldStatus if s not in ACCEPTED]
EOS_ENTRY = desc_gen.encode(Kind.EOS, seq=1)


def order(ref=1):
    return expect(encode("A", order_ref=ref, stock="AAPL    ", side="B"), ItchConfig(), Bitmap())[0]


def pkt(status=MoldStatus.OK, seq=100, n=1, **kw):
    return RingPacket(status, seq, msgs=tuple((seq + i, order(seq + i)) for i in range(n)), **kw)


def decoded(result):
    return [desc_gen.decode(e) for e in result.entries]


def kinds(result):
    return [d["kind"] for d in decoded(result)]


def flags(result):
    return [d["flags"] for d in decoded(result)]


# ---- One entry


def test_a_trade_event_gives_the_hand_written_entry():
    ev = Event(
        ItchClass.TRADE, 0x50, 44, b"unused", 0x3132, 0x3334, 0x414243444546, order_ref=0x6162636465666768,
        new_ref=0x7172737475767778, shares=0x8182838485868788, price=0x51525354, match=0x9192939495969798,
        side=0x42, stock=wire(b"AAPL    "), attribution=wire(b"ABCD"), printable=0x59, cross_type=0x4F,
    )  # fmt: skip
    p = RingPacket(MoldStatus.OK, 1, sof_ts=0x1112131415161718, epoch=0x5A, msgs=((0x0102030405060708, ev),))
    entry = expect_entries([p], overflow_pending=True).entries[0]
    assert stamp(entry, 0x21222324, 30) == HAND_ITCH


def test_a_full_raw_event_gives_the_hand_written_entry():
    raw = bytes(range(64))
    ev = Event(ItchClass.UNKNOWN, raw[0], 64, raw)
    p = RingPacket(MoldStatus.OK, 1, sof_ts=0x1112131415161718, epoch=0xA5, msgs=((0x0102030405060708, ev),))
    assert stamp(expect_entries([p]).entries[0], 0x21222324, 30) == HAND_RAW


@pytest.mark.parametrize(
    ("cls", "kind"),
    [
        (ItchClass.ORDER, Kind.ITCH),
        (ItchClass.TRADE, Kind.ITCH),
        (ItchClass.ADMIN, Kind.ITCH_RAW),
        (ItchClass.AUCTION, Kind.ITCH_RAW),
        (ItchClass.UNKNOWN, Kind.ITCH_RAW),
        (ItchClass.MALFORMED, Kind.ITCH_RAW),
    ],
)
def test_the_class_picks_the_kind(cls, kind):
    p = RingPacket(MoldStatus.OK, 1, msgs=((1, Event(cls, 0x41, 12, b"A" * 12)),))
    d = decoded(expect_entries([p]))[0]
    assert (d["kind"], d["itch_class"]) == (kind, cls)


def test_a_short_raw_message_is_zero_from_its_length_on():
    msg = bytes(range(1, 13))
    p = RingPacket(MoldStatus.OK, 1, msgs=((1, Event(ItchClass.ADMIN, 1, 12, msg)),))
    d = decoded(expect_entries([p]))[0]
    assert d["len"] == 12
    assert d["raw_lo"] == msg + bytes(26)
    assert d["raw_hi"] == bytes(26)


def test_the_q_message_keeps_its_eight_byte_shares_in_the_entry():
    ev = expect(encode("Q", shares=(1 << 64) - 2, cross_type="O"), ItchConfig(), Bitmap())[0]
    d = decoded(expect_entries([RingPacket(MoldStatus.OK, 1, msgs=((1, ev),))]))[0]
    assert d["shares"] == (1 << 64) - 2
    assert d["cross_type"] == ord("O")


# ---- Packet rules


def test_an_accepted_packet_keeps_its_messages_in_order():
    r = expect_entries([pkt(seq=100, n=3, sof_ts=7, epoch=9)])
    assert [d["seq"] for d in decoded(r)] == [100, 101, 102]
    assert [d["order_ref"] for d in decoded(r)] == [100, 101, 102]
    assert {d["sof_ts"] for d in decoded(r)} == {7}
    assert {d["cfg_epoch"] for d in decoded(r)} == {9}


@pytest.mark.parametrize("status", REJECTED, ids=[s.name for s in REJECTED])
def test_a_rejected_packet_leaves_nothing(status):
    r = expect_entries([pkt(status, n=3)])
    assert (r.entries, r.drop_pkts, r.drop_entries, r.drop_msgs) == ([], 0, 0, 0)


def test_the_gap_entry_follows_the_messages():
    r = expect_entries([pkt(seq=100, n=2, gap=5, sof_ts=7, epoch=9)])
    assert kinds(r) == [Kind.ITCH, Kind.ITCH, Kind.GAP]
    gap = decoded(r)[2]
    assert (gap["seq"], gap["first_seq"], gap["gap"]) == (95, 100, 5)
    assert (gap["sof_ts"], gap["cfg_epoch"]) == (7, 9)


def test_the_gap_entry_wraps_the_sequence_space():
    gap = decoded(expect_entries([RingPacket(MoldStatus.HEARTBEAT, 2, gap=5)]))[0]
    assert gap["seq"] == (1 << 64) - 3


def test_the_eos_entry_comes_last():
    r = expect_entries([RingPacket(MoldStatus.EOS, 100, gap=5, sof_ts=7, epoch=9)])
    assert kinds(r) == [Kind.GAP, Kind.EOS]
    eos = decoded(r)[1]
    assert (eos["seq"], eos["sof_ts"], eos["cfg_epoch"]) == (100, 7, 9)


def test_a_heartbeat_writes_only_its_gap():
    assert kinds(expect_entries([RingPacket(MoldStatus.HEARTBEAT, 100, gap=1)])) == [Kind.GAP]
    assert expect_entries([RingPacket(MoldStatus.HEARTBEAT, 100)]).entries == []


# ---- Drops


def test_a_dropped_packet_is_counted_whole():
    r = expect_entries([pkt(n=3, gap=1, dropped=True)])
    assert r.entries == []
    assert (r.drop_pkts, r.drop_entries, r.drop_msgs) == (1, 4, 3)  # 3 messages + GAP
    assert r.overflow_pending


def test_a_dropped_eos_packet_counts_its_eos_entry():
    r = expect_entries([RingPacket(MoldStatus.EOS, 1, gap=1, dropped=True)])
    assert (r.drop_pkts, r.drop_entries, r.drop_msgs) == (1, 2, 0)


def test_overflow_before_goes_on_the_next_committed_entry_only():
    r = expect_entries([pkt(n=2, dropped=True), pkt(n=2), pkt(n=1)])
    assert flags(r) == [FLAG_OVERFLOW_BEFORE, 0, 0]
    assert not r.overflow_pending


@pytest.mark.parametrize("status", REJECTED, ids=[s.name for s in REJECTED])
def test_a_rejected_packet_does_not_take_the_flag(status):
    r = expect_entries([pkt(dropped=True), pkt(status, n=2), pkt(n=1)])
    assert flags(r) == [FLAG_OVERFLOW_BEFORE]


def test_a_commit_without_entries_does_not_take_the_flag():
    r = expect_entries([pkt(dropped=True), RingPacket(MoldStatus.HEARTBEAT, 1), pkt()])
    assert flags(r) == [FLAG_OVERFLOW_BEFORE]


def test_a_gap_only_commit_takes_the_flag():
    r = expect_entries([pkt(dropped=True), RingPacket(MoldStatus.HEARTBEAT, 200, gap=1)])
    assert kinds(r) == [Kind.GAP]
    assert flags(r) == [FLAG_OVERFLOW_BEFORE]


def test_two_drops_give_one_flag():
    r = expect_entries([pkt(n=2, dropped=True), pkt(n=3, dropped=True), pkt()])
    assert flags(r) == [FLAG_OVERFLOW_BEFORE]
    assert (r.drop_pkts, r.drop_entries) == (2, 5)


def test_a_rejected_packet_that_did_not_fit_is_no_loss():
    r = expect_entries([pkt(MoldStatus.ABORT, n=3, dropped=True), pkt()])
    assert (r.drop_pkts, r.drop_entries, r.drop_msgs) == (0, 0, 0)
    assert flags(r) == [0]


def test_the_pending_flag_crosses_runs():
    first = expect_entries([pkt(dropped=True)])
    second = expect_entries([pkt()], first.overflow_pending)
    assert flags(second) == [FLAG_OVERFLOW_BEFORE]


def test_the_largest_packet_fits_the_default_fifo():
    # 1472-byte payload, 20-byte header, at least 2 + 6 bytes per message: 181, plus GAP and EOS.
    assert MAX_PKT_ENTRIES == 183
    assert MAX_PKT_ENTRIES <= 256
    pkg = (Path(__file__).resolve().parents[2] / "rtl" / "core" / "hft_pkg.sv").read_text(encoding="utf-8")
    assert f"RING_MAX_PKT_ENTRIES = {MAX_PKT_ENTRIES};" in pkg


def test_random_run_keeps_the_identities():
    """Over random packets: every OUT message of an accepted packet is committed or dropped (the
    identity with ITCH_OUT), the entries keep their order, and overflow_before marks exactly the first
    entry after a drop, as computed here from the run itself."""
    rng = random.Random(1)
    bitmap = Bitmap()
    bitmap.set(1)
    cfg = ItchConfig(class_filter=0x3F)
    stream = random_itch(rng, 5000, cfg, bitmap)
    packets, pos, seq = [], 0, 1
    while pos < len(stream):
        n = rng.randint(0, 20)
        evs = [expect(m.msg, cfg, bitmap) for m in stream[pos : pos + n]]
        msgs = tuple((seq + i, ev) for i, (ev, res) in enumerate(evs) if res.outcome == Outcome.OUT)
        status = rng.choice([*ACCEPTED, *ACCEPTED, *REJECTED])
        status = MoldStatus.OK if msgs and status in (MoldStatus.HEARTBEAT, MoldStatus.EOS) else status
        packets.append(RingPacket(status, seq, rng.choice((0, 0, 3)), msgs=msgs, dropped=rng.random() < 0.2))
        pos += n
        seq += n
    r = expect_entries(packets)

    accepted = [p for p in packets if p.status in ACCEPTED]
    out_msgs = sum(len(p.msgs) for p in accepted)
    d = decoded(r)
    committed_msgs = sum(k["kind"] in (Kind.ITCH, Kind.ITCH_RAW) for k in d)
    assert out_msgs == committed_msgs + r.drop_msgs > 0
    assert r.drop_pkts == sum(p.dropped for p in accepted) > 0
    want = [s for p in accepted if not p.dropped for s, _ in p.msgs]
    assert [k["seq"] for k in d if k["kind"] in (Kind.ITCH, Kind.ITCH_RAW)] == want

    expected_flags, pending = [], False
    for p in accepted:
        if p.dropped:
            pending = True
            continue
        n = len(p.msgs) + bool(p.gap) + (p.status == MoldStatus.EOS)
        for i in range(n):
            expected_flags.append(FLAG_OVERFLOW_BEFORE if pending and i == 0 else 0)
        pending = pending and n == 0
    assert flags(r) == expected_flags
    assert pending == r.overflow_pending


# ---- Ring protocol


@pytest.mark.parametrize(
    ("prod", "size_log2", "phase"),
    [(0, 4, 1), (15, 4, 1), (16, 4, 0), (31, 4, 0), (32, 4, 1), (1 << 20, 20, 0), ((1 << 32) + 1, 4, 1)],
)
def test_phase_starts_at_one_and_flips_on_each_pass(prod, size_log2, phase):
    assert phase_of(prod, size_log2) == phase


def test_slots_wrap_at_the_ring_size():
    assert slot_addr(0x1000, 0, 4) == 0x1000
    assert slot_addr(0x1000, 15, 4) == 0x1000 + 15 * 128
    assert slot_addr(0x1000, 16, 4) == 0x1000
    assert slot_addr(0x1000, 17, 4) == 0x1000 + 128


def test_stamp_fills_ring_idx_and_both_ctrl_bytes():
    e = stamp(EOS_ENTRY, 0x1_0000_0012, 4)  # pass 0x10000001 (odd): phase 0; ring_idx: low 32 bits
    assert e[16:20] == bytes.fromhex("12 00 00 00")
    assert (e[63], e[127]) == (0x01, 0x01)
    e = stamp(EOS_ENTRY, 0x22, 4)  # pass 2: phase 1
    assert (e[63], e[127]) == (0x81, 0x81)
    assert e[:16] == EOS_ENTRY[:16]


def run_ring(n, size_log2, every=1):
    """Write and read n entries through a ring, the consumer reading after each write."""
    ring = RingMemory(size_log2)
    cons = RingConsumer(ring.read, ring.write_cons, 0, size_log2, every)
    out = []
    for i in range(n):
        assert ring.write(desc_gen.encode(Kind.EOS, seq=i))
        entry = cons.poll()
        assert entry is not None
        out.append(desc_gen.decode(entry)["seq"])
        assert cons.poll() is None, "the old pass of the next slot must not look valid"
    return out


def test_the_consumer_reads_every_entry_over_several_passes():
    assert run_ring(100, 4) == list(range(100))


def test_empty_memory_is_not_valid():
    ring = RingMemory(4)
    assert RingConsumer(ring.read, ring.write_cons, 0, 4).poll() is None


def test_full_ring_waits_for_the_consumer():
    ring = RingMemory(2)
    cons = RingConsumer(ring.read, ring.write_cons, 0, 2, update_every=1)
    assert all(ring.write(EOS_ENTRY) for _ in range(4))
    assert ring.full() and not ring.write(EOS_ENTRY)
    assert cons.poll() is not None
    assert not ring.full() and ring.write(EOS_ENTRY)
    assert not ring.write(EOS_ENTRY)


def test_a_slow_cons_update_keeps_the_ring_full_longer():
    ring = RingMemory(4)
    cons = RingConsumer(ring.read, ring.write_cons, 0, 4)
    assert cons.update_every == 4  # N/4
    assert all(ring.write(EOS_ENTRY) for _ in range(16))
    for _ in range(3):
        assert cons.poll() is not None
    assert ring.full(), "CONS_IDX has not been written yet"
    assert cons.poll() is not None
    assert ring.cons == 4 and not ring.full()
    cons.poll()
    cons.flush()
    assert ring.cons == 5


def test_indexes_compare_across_the_32_bit_wrap():
    start = (1 << 32) - 2
    ring = RingMemory(2, start=start)
    cons = RingConsumer(ring.read, ring.write_cons, 0, 2, update_every=1, start=start)
    for i in range(20):
        assert ring.write(desc_gen.encode(Kind.EOS, seq=i))
        assert desc_gen.decode(cons.poll())["seq"] == i
    assert ring.cons == (start + 20) % (1 << 32)
    assert all(ring.write(EOS_ENTRY) for _ in range(4))
    assert ring.full()


@pytest.mark.parametrize("seen_line", [0, 1], ids=["line0_first", "line1_first"])
def test_an_entry_with_one_line_written_is_not_valid(seen_line):
    ring = RingMemory(4)
    cons = RingConsumer(ring.read, ring.write_cons, 0, 4)
    entry = stamp(EOS_ENTRY, 0, 4)
    lo, hi = (0, 64) if seen_line == 0 else (64, 128)
    ring.mem[lo:hi] = entry[lo:hi]
    assert cons.poll() is None
    assert cons.torn == 1
    ring.mem[:128] = entry
    assert cons.poll() == entry


def test_a_skipped_entry_is_an_error():
    ring = RingMemory(4)
    ring.mem[:128] = stamp(EOS_ENTRY, 16, 4)[:16] + stamp(EOS_ENTRY, 1, 4)[16:]  # ring_idx 1 in slot 0
    with pytest.raises(RingError, match="ring_idx"):
        RingConsumer(ring.read, ring.write_cons, 0, 4).poll()


def test_different_ctrl_bytes_are_an_error():
    ring = RingMemory(4)
    e = bytearray(stamp(EOS_ENTRY, 0, 4))
    e[127] |= 0x02
    ring.mem[:128] = e
    with pytest.raises(RingError, match="ctrl"):
        RingConsumer(ring.read, ring.write_cons, 0, 4).poll()


def test_a_wrong_version_is_an_error():
    ring = RingMemory(4)
    e = bytearray(stamp(EOS_ENTRY, 0, 4))
    e[63] = e[127] = 0x82
    ring.mem[:128] = e
    with pytest.raises(RingError, match="version"):
        RingConsumer(ring.read, ring.write_cons, 0, 4).poll()


def test_pacing_profiles():
    assert PACINGS["fast"].wait_after(1) == 1
    assert PACINGS["slow"].wait_after(1) == 64
    pausing = PACINGS["pausing"]
    assert (pausing.wait_after(0), pausing.wait_after(63), pausing.wait_after(64)) == (1, 1, 4096)
    assert Pacing(period=2).wait_after(64) == 2
