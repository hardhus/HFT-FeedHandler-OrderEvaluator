"""Golden model of moldudp64_rx (audit/11 §2.1-§2.4, §2.8). Pure Python, no simulator.

Decision: expect() reads the UDP payload as a byte string (struct, big-endian) and applies
the packet rules as one if-chain in the priority order of 11 §2.4. It never uses the RTL's
beat/lane layout, so a lane or byte-order bug in the framer cannot be mirrored here.

Protocol: Nasdaq MoldUDP64 v1.00. Header = session (10 B ASCII), sequence number of the
first message (8 B), message count (2 B); then `count` blocks of a 2-B length (not counting
itself) and that many bytes. count = 0 is a heartbeat, 0xFFFF the end of session; both carry
the next expected sequence number.

Generation: pack()/heartbeat()/eos() build packets from their fields. random_feed() mixes
every outcome category round-robin, so coverage does not depend on the seed; it tracks the
expected sequence number from the outcome it means (its intent), not by calling expect().
"""

import random
import struct
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

HDR = struct.Struct(">10sQH")  # session, sequence number, message count
HDR_LEN = HDR.size  # 20
COUNT_EOS = 0xFFFF
MIN_LEN = 6  # shorter messages: UNSUPPORTED (more than one block boundary per 8-B beat)
MAX_MSG = 64  # longer messages are skipped and counted (oversize)
SEQ_MOD = 1 << 64
UDP_PAYLOAD_MAX = 1472  # 1500-B MTU - 20 (IPv4) - 8 (UDP)

# TotalView-ITCH 5.0 message lengths, type byte included (Nasdaq TotalView-ITCH 5.0
# specification, message format tables), written out by hand.
ITCH50_LEN = {
    "S": 12, "R": 39, "H": 25, "Y": 20, "L": 26, "V": 35, "W": 12, "K": 28, "J": 35, "h": 21,
    "A": 36, "F": 40, "E": 31, "C": 36, "X": 23, "D": 19, "U": 35,
    "P": 44, "Q": 40, "B": 19, "I": 50, "N": 20, "O": 48,
}  # fmt: skip


class MoldStatus(IntEnum):
    """Packet outcome = mold_status_e in hft_pkg.sv (checked by test_status_codes_match_rtl)."""

    OK = 0
    HEARTBEAT = 1
    EOS = 2
    DUP = 3
    DISABLED = 4
    OTHER_FEED = 5
    ABORT = 6
    RUNT = 7
    SESSION = 8
    UNSUPPORTED = 9
    MALFORMED = 10


ACCEPTED = (MoldStatus.OK, MoldStatus.HEARTBEAT, MoldStatus.EOS)  # the packet's messages are final


@dataclass(frozen=True)
class FeedConfig:
    enable: bool = True
    session_check: bool = True
    session: bytes = b"SESSION001"
    filter_mask: int = 0x01  # bit i: F2 filter entry i belongs to this feed (A and B line)


@dataclass(frozen=True)
class FeedState:
    expected: int = 0  # next expected sequence number (E)
    synced: bool = False
    eos: bool = False


@dataclass(frozen=True)
class MoldResult:
    """The packet event, and msgs = the messages on m_msg_* before it, as (seq, bytes);
    m_pkt_msgs = len(msgs) for every status. dup_msgs/oversize count the blocks that completed
    and were not output for that reason, only in packets whose framing was checked (OK,
    HEARTBEAT, EOS, DUP); elsewhere the lengths may be corrupt and the messages come again, so
    they are 0. gap is reported only when the packet is accepted in sync. Over a run the
    accepted packets then account for every sequence number once:
    E_end - E_sync = sum(msgs of accepted) + sum(oversize) + sum(gap)."""

    status: MoldStatus
    msgs: tuple[tuple[int, bytes], ...] = ()
    gap: int = 0
    dup_msgs: int = 0
    oversize: int = 0
    synced_now: bool = False


def seq_diff(a: int, b: int) -> int:
    """a - b as a signed 64-bit number (serial number arithmetic, 11 §2.4)."""
    d = (a - b) % SEQ_MOD
    return d - SEQ_MOD if d >= SEQ_MOD // 2 else d


def resync(state: FeedState, learn: bool, seq: int) -> FeedState:
    """cfg_resync: learn the next packet's sequence number, or set E = seq."""
    return FeedState(state.expected, False, False) if learn else FeedState(seq % SEQ_MOD, True, False)


def _framed(payload: bytes, cfg: FeedConfig, state: FeedState) -> tuple[MoldResult, FeedState]:
    """Everything after DISABLED, OTHER_FEED and ABORT: RUNT, SESSION, UNSUPPORTED, MALFORMED,
    DUP, then accepted."""
    if len(payload) < HDR_LEN:
        return MoldResult(MoldStatus.RUNT), state
    session, seq, count = HDR.unpack_from(payload)
    if cfg.session_check and session != cfg.session:
        return MoldResult(MoldStatus.SESSION), state
    E, synced = state.expected, state.synced
    d = seq_diff(seq, E)

    if count in (0, COUNT_EOS):
        if len(payload) != HDR_LEN:
            return MoldResult(MoldStatus.MALFORMED), state
        if synced and d < 0:
            return MoldResult(MoldStatus.DUP), state
        status = MoldStatus.EOS if count == COUNT_EOS else MoldStatus.HEARTBEAT
        gap = d if synced and d > 0 else 0
        eos = True if count == COUNT_EOS else state.eos
        return MoldResult(status, gap=gap, synced_now=not synced), FeedState(seq, True, eos)

    msgs, dup, oversize, pos, bad = [], 0, 0, HDR_LEN, None
    for i in range(count):
        if pos + 2 > len(payload):
            bad = MoldStatus.MALFORMED  # payload ends in or before the length field
            break
        n = int.from_bytes(payload[pos : pos + 2], "big")
        if n < MIN_LEN:
            bad = MoldStatus.UNSUPPORTED
            break
        if pos + 2 + n > len(payload):
            bad = MoldStatus.MALFORMED  # payload ends inside the message
            break
        msg_seq = (seq + i) % SEQ_MOD
        # Duplicates are the first E - S messages of a packet that starts before E (11 §2.4).
        # Not seq_diff(msg_seq, E) < 0: in a packet 2^63 - 1 ahead, the messages after the first
        # would pass half the sequence space and count as old.
        if synced and d < 0 and i < -d:
            dup += 1
        elif n > MAX_MSG:
            oversize += 1
        else:
            msgs.append((msg_seq, payload[pos + 2 : pos + 2 + n]))
        pos += 2 + n
    if bad is None and pos != len(payload):
        bad = MoldStatus.MALFORMED  # bytes after the last block
    if bad is not None:
        return MoldResult(bad, tuple(msgs)), state  # untrusted lengths: no dup/oversize counts
    if synced and d < 0 and seq_diff(seq + count, E) <= 0:
        return MoldResult(MoldStatus.DUP, (), 0, dup, oversize), state
    gap = d if synced and d > 0 else 0
    result = MoldResult(MoldStatus.OK, tuple(msgs), gap, dup, oversize, not synced)
    return result, FeedState((seq + count) % SEQ_MOD, True, False)


def expect(
    payload: bytes, abort: bool, filter_idx: int, cfg: FeedConfig, state: FeedState
) -> tuple[MoldResult, FeedState]:
    """Reference outcome of one packet (11 §2.4) and the feed state after it.

    payload: the bytes moldudp64_rx received (fewer than the UDP length when F2 aborts a
    truncated frame). abort: tuser on the last beat. An aborted packet outputs the messages
    the same bytes would output without the abort (the RTL learns of the abort on the last
    beat, after most of them are out); the state does not change.
    """
    if not cfg.enable:
        return MoldResult(MoldStatus.DISABLED), state
    if not (cfg.filter_mask >> filter_idx) & 1:
        return MoldResult(MoldStatus.OTHER_FEED), state
    result, after = _framed(payload, cfg, state)
    if abort:
        return MoldResult(MoldStatus.ABORT, result.msgs), state
    return result, after


# ---------------------------------------------------------------------------------------
# Packet building (test stimulus)


def pack(session: bytes, seq: int, msgs: list[bytes]) -> bytes:
    """A MoldUDP64 packet with these messages; count = len(msgs)."""
    blocks = b"".join(len(m).to_bytes(2, "big") + m for m in msgs)
    return HDR.pack(session, seq % SEQ_MOD, len(msgs)) + blocks


def heartbeat(session: bytes, seq: int) -> bytes:
    return HDR.pack(session, seq % SEQ_MOD, 0)


def eos(session: bytes, seq: int) -> bytes:
    return HDR.pack(session, seq % SEQ_MOD, COUNT_EOS)


def itch_like(rng: random.Random, typ: str) -> bytes:
    """A message of ITCH 5.0 type typ with its real length and random content."""
    return typ.encode() + rng.randbytes(ITCH50_LEN[typ] - 1)


def block_offsets(msgs: list[bytes]) -> list[int]:
    """Payload offset of each block's length field in pack(.., msgs)."""
    out, pos = [], HDR_LEN
    for m in msgs:
        out.append(pos)
        pos += 2 + len(m)
    return out


# ---------------------------------------------------------------------------------------
# Random feed

GAPS = (1, 1000, 1 << 40)
OVERSIZE = (65, 1000, 1400)


@dataclass(frozen=True)
class FeedPacket:
    payload: bytes
    abort: bool  # tuser on the last beat
    filter_idx: int
    enable: bool  # cfg_feed_en at this packet's first beat
    category: str
    intent: MoldStatus  # what the generator meant to produce (expect() must agree)


Make = Callable[[], tuple[bytes, bool, int, bool]]  # -> (payload, abort, filter_idx, enable)


class _FeedGen:
    """Packet factory for one config. E/synced follow the intent of each packet made."""

    def __init__(self, rng: random.Random, cfg: FeedConfig) -> None:
        self.rng, self.cfg = rng, cfg
        self.mine = [i for i in range(8) if cfg.filter_mask >> i & 1]
        self.others = [i for i in range(8) if not cfg.filter_mask >> i & 1]
        if not cfg.enable or not self.mine:
            raise ValueError("random_feed needs an enabled feed with at least one filter entry")
        self.E = rng.randrange(SEQ_MOD)

    # ---- pieces
    def idx(self) -> int:
        return self.rng.choice(self.mine)

    def itch_msgs(self, lo: int = 1, hi: int = 20) -> list[bytes]:
        return [itch_like(self.rng, self.rng.choice(list(ITCH50_LEN))) for _ in range(self.rng.randint(lo, hi))]

    def other_session(self) -> bytes:
        while True:
            s = self.rng.randbytes(10)
            if s != self.cfg.session:
                return s

    def accept(self, seq: int, count: int) -> None:
        """A normal packet was accepted: E = seq + count."""
        self.E = (seq + count) % SEQ_MOD

    # ---- packets (each returns (payload, abort, filter_idx, enable) and updates E)
    def in_order(self, msgs: list[bytes] | None = None) -> bytes:
        msgs = self.itch_msgs() if msgs is None else msgs
        seq = self.E
        self.accept(seq, len(msgs))
        return pack(self.cfg.session, seq, msgs)

    def untouched(self, msgs: list[bytes] | None = None) -> bytes:
        """An in-order packet that the outcome will reject: E stays."""
        msgs = self.itch_msgs() if msgs is None else msgs
        return pack(self.cfg.session, self.E, msgs)

    def gap(self, g: int) -> bytes:
        msgs = self.itch_msgs()
        seq = (self.E + g) % SEQ_MOD
        self.accept(seq, len(msgs))
        return pack(self.cfg.session, seq, msgs)

    def dup_full(self) -> bytes:
        msgs = self.itch_msgs(1, 10)
        return pack(self.cfg.session, self.E - len(msgs) - self.rng.randint(0, 100), msgs)

    def dup_partial(self) -> bytes:
        msgs = self.itch_msgs(2, 10)
        seq = (self.E - self.rng.randint(1, len(msgs) - 1)) % SEQ_MOD
        self.accept(seq, len(msgs))
        return pack(self.cfg.session, seq, msgs)

    def hb(self, ofs: int, end: bool = False) -> bytes:
        seq = (self.E + ofs) % SEQ_MOD
        if ofs >= 0:
            self.E = seq
        return (eos if end else heartbeat)(self.cfg.session, seq)

    def align(self, start: int, end: int) -> bytes:
        """A block whose length field starts in lane `start` and whose last byte is in lane `end`
        (lanes of the 8-B beats, byte 0 of the payload in lane 0). Lane 7: split length field."""
        rng = self.rng
        n1 = MIN_LEN + (start - (HDR_LEN + 2) - MIN_LEN) % 8 + 8 * rng.randint(0, 6)  # filler
        n2 = MIN_LEN + (end - start - 1 - MIN_LEN) % 8 + 8 * rng.randint(0, 6)
        msgs = [rng.randbytes(n1), rng.randbytes(n2)] + self.itch_msgs(0, 3)
        return self.in_order(msgs)

    def unsupported(self, n: int) -> bytes:
        rng = self.rng
        msgs = self.itch_msgs(0, 3) + [rng.randbytes(n)] + self.itch_msgs(0, 3)
        return self.untouched(msgs)

    def oversize(self, n: int) -> bytes:
        msgs = [self.rng.randbytes(n)]
        while len(pack(b"", 0, msgs)) + 52 <= UDP_PAYLOAD_MAX and len(msgs) < 6:
            msgs.insert(self.rng.randrange(len(msgs) + 1), itch_like(self.rng, self.rng.choice(list(ITCH50_LEN))))
        return self.in_order(msgs)

    def cut(self, in_length: bool) -> bytes:
        """An in-order packet cut inside a block's length field (or right before it), or inside
        its message: the payload ends before `count` blocks are complete."""
        msgs = self.itch_msgs()
        k = self.rng.randrange(len(msgs))
        ofs = block_offsets(msgs)[k]
        end = ofs + self.rng.randint(0, 1) if in_length else self.rng.randint(ofs + 2, ofs + 1 + len(msgs[k]))
        return self.untouched(msgs)[:end]

    def categories(self) -> list[tuple[str, int, MoldStatus, Make]]:
        """(name, weight, intended status, make)."""
        rng, S = self.rng, MoldStatus

        def f(build: Callable[[], bytes], abort: bool = False, enable: bool = True,
              idx: Callable[[], int] | None = None) -> Make:  # fmt: skip
            return lambda: (build(), abort, (idx or self.idx)(), enable)

        cats = [
            ("ok", 4, S.OK, f(self.in_order)),
            ("dup_full", 1, S.DUP, f(self.dup_full)),
            ("dup_partial", 1, S.OK, f(self.dup_partial)),
            ("hb_in_order", 1, S.HEARTBEAT, f(lambda: self.hb(0))),
            ("hb_gap", 1, S.HEARTBEAT, f(lambda: self.hb(rng.randint(1, 1000)))),
            ("hb_old", 1, S.DUP, f(lambda: self.hb(-rng.randint(1, 1000)))),
            ("eos", 1, S.EOS, f(lambda: self.hb(0, end=True))),
            ("abort", 1, S.ABORT, f(self.untouched, abort=True)),
            ("abort_cut", 1, S.ABORT, f(lambda: self.untouched()[: rng.randint(1, 200)], abort=True)),
            ("runt", 1, S.RUNT, f(lambda: self.untouched()[: rng.randint(1, HDR_LEN - 1)])),
            ("disabled", 1, S.DISABLED, f(self.untouched, enable=False)),
            ("malformed_trailing", 1, S.MALFORMED, f(lambda: self.untouched() + rng.randbytes(rng.randint(1, 7)))),
            ("malformed_cut_len", 1, S.MALFORMED, f(lambda: self.cut(in_length=True))),
            ("malformed_cut_msg", 1, S.MALFORMED, f(lambda: self.cut(in_length=False))),
            ("malformed_hb", 1, S.MALFORMED,
             f(lambda: (rng.choice([heartbeat, eos]))(self.cfg.session, self.E) + rng.randbytes(rng.randint(1, 8)))),
        ]  # fmt: skip
        cats += [(f"gap_{g}", 1, S.OK, f(lambda g=g: self.gap(g))) for g in GAPS]
        cats += [(f"oversize_{n}", 1, S.OK, f(lambda n=n: self.oversize(n))) for n in OVERSIZE]
        cats += [(f"unsupported_{n}", 1, S.UNSUPPORTED, f(lambda n=n: self.unsupported(n))) for n in range(MIN_LEN)]
        cats += [
            (f"align_{s}_{e}", 1, S.OK, f(lambda s=s, e=e: self.align(s, e))) for s in range(8) for e in range(8)
        ]
        if self.cfg.session_check:
            bad = lambda: pack(self.other_session(), self.E, self.itch_msgs())  # noqa: E731
            cats.append(("session", 1, S.SESSION, f(bad)))
        for i in self.others:  # every filter entry of another feed
            cats.append((f"other_feed_{i}", 1, S.OTHER_FEED, f(self.untouched, idx=lambda i=i: i)))
        for i in self.mine:  # every line of the feed (A/B)
            cats.append((f"ok_filter_{i}", 1, S.OK, f(self.in_order, idx=lambda i=i: i)))
        return cats


def random_feed(rng: random.Random, n: int, cfg: FeedConfig) -> list[FeedPacket]:
    """n packets for a feed that starts out of sync (reset state). The first packet is in
    order and synchronizes; then each round holds every category `weight` times in random
    order, so every category appears once n covers one round, whatever the seed."""
    gen = _FeedGen(rng, cfg)
    out = [FeedPacket(gen.in_order(), False, gen.idx(), True, "sync", MoldStatus.OK)]
    cats = gen.categories()
    schedule = [c for c in cats for _ in range(c[1])]
    while len(out) < n:
        rnd = schedule[:]
        rng.shuffle(rnd)
        for name, _, intent, make in rnd[: n - len(out)]:
            payload, abort, idx, enable = make()
            out.append(FeedPacket(payload, abort, idx, enable, name, intent))
    return out[:n]

