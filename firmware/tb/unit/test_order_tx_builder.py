"""order_tx_builder against the order model and the simulated exchange (tb/model/order.py, tb/common/exchange.py;
audit/14 §2.6, §2.10; decisions 12, 26).

Cycles: inputs driven for step s are sampled at edge s; the monitor reads the outputs after edge s. In step s:
m_axis_tx_tready is driven; a beat on the bus after edge s-1 is taken at edge s if tready; an order is offered
(s_ord_valid) and taken if s_ord_ready (read in ReadOnly, as it depends on tready); a config write is checked at
edge s. The expected bus after edge s: the frame of the order taken at s from its first beat, else the next beat
of the frame on the bus. Every beat is compared byte for byte with order.frame() of the order and the config in
force when it was taken, and every finished frame goes through Exchange (scapy headers, checksum, SoupBinTCP,
OUCH fields, UserRefNum + 1). ORDER_SENT is checked after the edge that takes the first beat, every field, with
that step's ts_now.
"""

import random
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import cocotb
import pytest
from cocotb.triggers import ReadOnly, RisingEdge

from common.clocks import start_clock
from common.coverage import Coverage
from common.exchange import Exchange
from common.runner import run_cocotb
from model.itch import wire
from model.order import (
    FRAME_LEN,
    PRICE_LIMIT_MAX,
    USER_REF_MAX,
    OrderReq,
    Source,
    TxConfig,
    frame,
    hw_clordid,
    ouch_of,
    template,
    tx_config_errors,
)

PERIOD_PS = 6400
N_RANDOM = 5000  # orders of tc_random_golden
CFG_LAT = 2  # a write at edge k is decided at edge k + CFG_LAT (A24)
BEATS = 12
SPACES = b" " * 14
CFG = TxConfig(template())


def order(source=Source.HOST, **kw) -> OrderReq:
    base = {
        "source": source, "side": ord("B"), "qty": 100, "price": 1_000_000, "locate": 42, "stock": b"MSFT    ",
        "tif": ord("0"), "clordid": b"CL 1          " if source == Source.HOST else None,
        "rule": 0, "seq": random.getrandbits(64), "sof_ts": random.getrandbits(64), "epoch": random.getrandbits(8),
    }  # fmt: skip
    return OrderReq(**{**base, **kw})


def random_order() -> OrderReq:
    kw = {
        "side": random.choice(b"BS"), "qty": random.choice((1, 100, 999_999, random.randrange(1, 10**6))),
        "price": random.choice((1, PRICE_LIMIT_MAX, random.randrange(1, PRICE_LIMIT_MAX))),
        "locate": random.getrandbits(16), "stock": random.randbytes(8), "tif": random.choice(b"035E"),
    }  # fmt: skip
    if random.random() < 0.5:
        return order(Source.RULE, rule=random.randrange(8), **kw)
    return order(clordid=bytes(random.choice(b"ABCxyz0129 ") for _ in range(14)), **kw)


def put(t: bytes, ofs: int, value: bytes) -> bytes:
    return t[:ofs] + value + t[ofs + len(value) :]


def refix(t: bytes) -> bytes:
    hdr = t[14:24] + bytes(2) + t[26:34]
    total = sum(int.from_bytes(hdr[i : i + 2], "big") for i in range(0, 20, 2))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return put(t, 24, (~total & 0xFFFF).to_bytes(2, "big"))


T = CFG.template
BAD = {  # one bad field each (decision 26)
    "display": TxConfig(T, display=ord("Z")), "capacity": TxConfig(T, capacity=ord("X")),
    "ise": TxConfig(T, ise=ord("Y")), "cross_type": TxConfig(T, cross_type=ord("O")),
    "ethertype": TxConfig(put(T, 12, bytes.fromhex("86dd"))), "ver_ihl": TxConfig(refix(put(T, 14, bytes([0x46])))),
    "ip_len": TxConfig(refix(put(T, 16, (79).to_bytes(2, "big")))),
    "flags_frag": TxConfig(refix(put(T, 20, bytes(2)))), "proto": TxConfig(refix(put(T, 23, bytes([6])))),
    "ip_csum": TxConfig(put(T, 25, bytes([T[25] ^ 1]))), "udp_len": TxConfig(put(T, 38, (59).to_bytes(2, "big"))),
    "udp_csum": TxConfig(put(T, 40, bytes([0x12, 0x34]))),
}  # fmt: skip
BINS = [
    *[f"refused_{k}" for k in BAD], "back_to_back", "stall_first", "stall_mid", "stall_last", "cfg_between_frames",
    "hw_clordid", "host_clordid", "hex_af", "rule_7", "cfg_same_edge_as_take", "sent_ts_after_stall", "cfg_overlap",
]  # fmt: skip


@dataclass
class Env:
    dut: object
    cov: Coverage | None = None
    s: int = 0
    cfg: TxConfig | None = None  # in force (None: none since reset)
    user_ref: int = 1
    cur: bytes | None = None  # frame on the bus
    beat: int = 0
    req: OrderReq | None = None  # its order
    ref: int = 0
    exp: dict = field(default_factory=dict)  # edge -> (beat bytes | None, tkeep, tlast, sent | None, cfg flags)
    bus: dict = field(default_factory=dict)  # edge -> tvalid seen
    beats: list = field(default_factory=list)  # bytes of the frame being received
    frames: list = field(default_factory=list)  # (ref, frame bytes) received
    sent: list = field(default_factory=list)  # (edge, ref) ORDER_SENT seen
    exchange: Exchange | None = None
    cfg_valid: bool = False
    pend: dict = field(default_factory=dict)  # edge -> config written CFG_LAT edges before
    paused: bool = False
    stalled_first: bool = False

    def hit(self, name: str) -> None:
        if self.cov is not None:
            self.cov.hit(name)

    def idle_inputs(self) -> None:
        d = self.dut
        d.s_ord_valid.value = 0
        d.cfg_wr.value = 0
        d.cfg_template.value = random.getrandbits(8 * 42)
        for k in ("display", "capacity", "ise", "cross_type"):
            getattr(d, f"cfg_{k}").value = random.getrandbits(8)
        o = random_order()
        self.drive_order(o, random.getrandbits(32))

    def drive_order(self, o: OrderReq, ref: int) -> None:
        d = self.dut
        d.s_ord_user_ref.value = ref
        d.s_ord_source.value = int(o.source)
        for k in ("rule", "side", "qty", "price", "locate", "tif", "seq", "sof_ts", "epoch"):
            getattr(d, f"s_ord_{k}").value = getattr(o, k)
        d.s_ord_stock.value = wire(o.stock)
        d.s_ord_clordid.value = wire(o.clordid if o.clordid is not None else random.randbytes(14))

    async def step(self, o: OrderReq | None = None, ready: bool = True, cfg: TxConfig | None = None) -> bool:
        """One step; returns True if o was taken."""
        d = self.dut
        s = self.s + 1
        self.idle_inputs()
        ts = random.getrandbits(64)
        d.ts_now.value = ts
        d.m_axis_tx_tready.value = int(ready)
        if o is not None:
            d.s_ord_valid.value = 1
            self.drive_order(o, self.user_ref)
        if cfg is not None:
            d.cfg_wr.value = 1
            d.cfg_template.value = int.from_bytes(cfg.template.ljust(42, b"\0")[:42], "little")
            for k in ("display", "capacity", "ise", "cross_type"):
                getattr(d, f"cfg_{k}").value = getattr(cfg, k)
        await ReadOnly()
        taken = o is not None and bool(int(d.s_ord_ready.value))

        # the bus: a beat after edge s-1 is taken now if ready
        sent = None
        handshake = self.bus.get(s - 1, False) and ready
        if self.cur is not None and self.bus.get(s - 1, False) and not ready:
            self.hit("stall_first" if self.beat == 0 else "stall_last" if self.beat == BEATS - 1 else "stall_mid")
        if handshake:
            if self.beat == 0:
                sent = (self.req, self.ref, ts)
                if self.stalled_first:
                    self.hit("sent_ts_after_stall")
            self.beats.append(self.cur[8 * self.beat : 8 * self.beat + 8])
            self.beat += 1
            if self.beat == BEATS:
                self._frame_done()
        self.stalled_first = self.cur is not None and self.beat == 0 and not handshake
        if taken:
            assert self.cur is None, f"step {s}: taken while a frame is on the bus"
            if self.cfg is not None and s in self.pend:
                self.hit("cfg_same_edge_as_take")
            if handshake and self.frames and self.frames[-1][0] == self.ref:
                self.hit("back_to_back")  # the last beat went in this step
            self._take(o)

        # the config written CFG_LAT steps ago is decided now (after the take: a take in the same step uses the
        # old one)
        if cfg is not None:
            if self.pend:
                self.hit("cfg_overlap")  # an earlier write is still being checked
            self.pend[s + CFG_LAT] = cfg
        cfg = self.pend.pop(s, None)
        refused = False
        if cfg is not None:
            errors = tx_config_errors(cfg)
            if errors:
                refused = True
                for k in errors:
                    if f"refused_{k}" in BINS:
                        self.hit(f"refused_{k}")
            else:
                if self.cfg is not None and self.cfg != cfg:
                    self.hit("cfg_between_frames")
                self.cfg = cfg
                self.cfg_valid = True
                self.exchange = self.exchange or Exchange(cfg.template, self.user_ref)
                self.exchange.template = cfg.template

        # expected outputs after edge s
        beat = self.cur[8 * self.beat : 8 * self.beat + 8] if self.cur is not None else None
        self.bus[s] = beat is not None
        self.exp[s] = (beat, self.beat, sent, self.cfg_valid, refused)
        await RisingEdge(d.clk)
        self.s = s
        return taken

    def _take(self, o: OrderReq) -> None:
        assert self.cfg is not None, "an order taken with no config"
        self.req, self.ref = o, self.user_ref
        ou = ouch_of(o, self.user_ref, self.cfg)
        self.cur = frame(self.cfg, ou)
        assert len(self.cur) == FRAME_LEN
        self.cur = self.cur.ljust(8 * BEATS, b"\0")
        self.beat = 0
        self.hit("hw_clordid" if o.source == Source.RULE else "host_clordid")
        if o.source == Source.RULE and o.rule == 7:
            self.hit("rule_7")
        if any(c in b"ABCDEF" for c in hw_clordid(o.rule, self.user_ref)[3:11]) and o.source == Source.RULE:
            self.hit("hex_af")
        self.user_ref += 1

    def _frame_done(self) -> None:
        got = b"".join(self.beats)[:FRAME_LEN]
        want = self.cur[:FRAME_LEN]
        assert got == want, f"frame {self.ref}: {got.hex()} != {want.hex()}"
        if self.exchange is not None:
            self.exchange.template = want[:42]  # the config in force when the order was taken
            o = self.exchange.receive(got)
            assert o.user_ref == self.ref
        self.frames.append((self.ref, got))
        self.beats = []
        self.cur = None
        self.beat = 0

    async def monitor(self) -> None:
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            if not self.paused and self.s in self.exp:
                self._check(self.exp.pop(self.s))

    def _check(self, e) -> None:
        d, s = self.dut, self.s
        beat, idx, sent, cfg_valid, refused = e
        assert int(d.m_axis_tx_tvalid.value) == (beat is not None), f"edge {s}: tvalid"
        if beat is not None:
            got = int(d.m_axis_tx_tdata.value).to_bytes(8, "little")
            keep = int(d.m_axis_tx_tkeep.value)
            last = int(d.m_axis_tx_tlast.value)
            want_keep = 0x0F if idx == BEATS - 1 else 0xFF
            assert (keep, last) == (want_keep, int(idx == BEATS - 1)), f"edge {s}: tkeep/tlast of beat {idx}"
            n = 4 if idx == BEATS - 1 else 8
            assert got[:n] == beat[:n], f"edge {s}: beat {idx} {got.hex()} != {beat.hex()}"
        assert int(d.m_sent_valid.value) == (sent is not None), f"edge {s}: m_sent_valid"
        if sent is not None:
            o, ref, ts = sent
            clordid = o.clordid if o.source == Source.HOST else hw_clordid(o.rule, ref)
            got = (int(d.m_sent_user_ref.value), int(d.m_sent_source.value), int(d.m_sent_rule.value),
                   int(d.m_sent_side.value), int(d.m_sent_qty.value), int(d.m_sent_price.value),
                   int(d.m_sent_locate.value), int(d.m_sent_stock.value), int(d.m_sent_tif.value),
                   int(d.m_sent_clordid.value), int(d.m_sent_seq.value), int(d.m_sent_sof_ts.value),
                   int(d.m_sent_epoch.value), int(d.m_sent_tx_ts.value))  # fmt: skip
            want = (ref, int(o.source), o.rule, o.side, o.qty, o.price, o.locate, wire(o.stock), o.tif, wire(clordid),
                    o.seq, o.sof_ts, o.epoch, ts)  # fmt: skip
            assert got == want, f"edge {s}: ORDER_SENT {got} != {want}"
            self.sent.append((s, ref))
        assert int(d.st_cfg_valid.value) == cfg_valid, f"edge {s}: st_cfg_valid"
        assert int(d.ev_cfg_refused.value) == refused, f"edge {s}: ev_cfg_refused"

    async def configure(self, cfg: TxConfig) -> None:
        """Write cfg and wait until it is decided."""
        await self.step(cfg=cfg)
        for _ in range(CFG_LAT):
            await self.step()

    async def send(self, o: OrderReq, ready_p: float = 1.0) -> None:
        """Offer o until it is taken."""
        while not await self.step(o, ready=random.random() < ready_p):
            pass

    async def drain(self) -> None:
        while self.cur is not None:
            await self.step()
        await self.step()
        await self.step()


async def start(dut, cov: Coverage | None = None) -> Env:
    e = Env(dut, cov)
    e.idle_inputs()
    dut.m_axis_tx_tready.value = 0
    dut.ts_now.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    cocotb.start_soon(e.monitor())
    return e


# ---------------------------------------------------------------- directed


@cocotb.test()
async def tc_reset_state(dut):
    """After reset: no config, nothing on the bus."""
    e = await start(dut)
    await e.step()
    await e.step()
    assert e.frames == [] and not e.cfg_valid


@cocotb.test()
async def tc_hand_frame(dut):
    """The worked example of docs/protocol/order_msg.md, byte for byte, behind the template: UserRefNum 1, buy 100
    AAPL at 150.25, Day, hardware rule 0; first beat after the take edge, ORDER_SENT after the first beat."""
    e = await start(dut)
    await e.configure(CFG)
    o = order(Source.RULE, rule=0, side=ord("B"), qty=100, stock=b"AAPL    ", price=1502500, tif=ord("0"))
    k = e.s + 1
    assert await e.step(o)
    await e.drain()
    doc = bytes.fromhex(
        "00 30 55 4f 00 00 00 01 42 00 00 00 64 41 41 50"
        "4c 20 20 20 20 00 00 00 00 00 16 ed 24 30 59 50"
        "4e 4e 48 57 30 30 30 30 30 30 30 30 31 20 20 20"
        "00 00"
    )
    ((ref, got),) = e.frames
    assert ref == 1 and got == CFG.template + doc
    assert e.sent == [(k + 1, 1)]


@cocotb.test()
async def tc_back_to_back(dut):
    """The next order is taken in the cycle the last beat goes: 12 cycles a frame, no gap."""
    e = await start(dut)
    await e.configure(CFG)
    takes = []
    for i in range(4):
        o = order(qty=i + 1)
        while True:
            if await e.step(o):
                takes.append(e.s)
                break
    await e.drain()
    assert [b - a for a, b in zip(takes, takes[1:], strict=False)] == [BEATS] * 3
    assert [ref for ref, _ in e.frames] == [1, 2, 3, 4]


@cocotb.test()
async def tc_backpressure(dut):
    """tready low on the first, a middle and the last beat: the beat holds; ORDER_SENT waits for the first beat to
    be taken and carries that cycle's ts_now."""
    e = await start(dut)
    await e.configure(CFG)
    assert await e.step(order(), ready=False)
    await e.step(ready=False)  # first beat stalled
    await e.step(ready=False)
    await e.step()  # taken: ORDER_SENT now
    for b in range(1, BEATS):
        if b in (5, BEATS - 1):
            await e.step(ready=False)
        await e.step()
    await e.drain()
    assert len(e.frames) == 1 and len(e.sent) == 1


@cocotb.test()
async def tc_config_checks(dut):
    """Decision 26: every bad field alone is refused and the previous config stays; st_cfg_valid only after a good
    one; a good one applies from the next frame taken."""
    e = await start(dut)
    for k, bad in BAD.items():
        await e.step(cfg=bad)
        assert not e.cfg_valid, k
    await e.step(cfg=CFG)
    other = TxConfig(template(dst_ip="10.9.9.9", dport=7777), display=ord("N"), capacity=ord("A"))
    for k, bad in BAD.items():
        await e.step(cfg=bad)
    await e.send(order(qty=1))
    await e.step(cfg=other)  # during the first frame: applies from the next one
    await e.send(order(qty=2))
    await e.drain()
    assert [f[:42] for _, f in e.frames] == [CFG.template, other.template]
    assert [f[72] for _, f in e.frames] == [ord("Y"), ord("N")]


@cocotb.test()
async def tc_config_latency(dut):
    """A24: a write at edge k is decided at edge k+2: a take at edge k+2 uses the previous config, one at k+3 the
    new one; st_cfg_valid and ev_cfg_refused change after edge k+2 (the monitor checks every edge); writes on
    consecutive cycles are each decided, in order."""
    e = await start(dut)
    other = TxConfig(template(dport=6000), display=ord("N"))
    await e.step(cfg=CFG)
    await e.step()
    await e.step()
    assert e.cfg_valid
    await e.step(cfg=other)
    await e.step()
    assert await e.step(order(qty=1))  # edge k+2: still CFG
    await e.drain()
    await e.step(cfg=CFG)
    await e.step()
    await e.step()
    assert await e.step(order(qty=2))  # edge k+3: CFG again (other was in force)
    await e.drain()
    await e.step(cfg=other)
    await e.step(cfg=BAD["proto"])
    await e.step(cfg=CFG)
    assert await e.step(order(qty=3))  # edge k+3: other decided, the refusal and CFG still in the pipe
    await e.drain()
    await e.send(order(qty=4))
    await e.drain()
    assert [f[:42] for _, f in e.frames] == [CFG.template, CFG.template, other.template, CFG.template]
    assert [f[72] for _, f in e.frames] == [ord("Y"), ord("Y"), ord("N"), ord("Y")]


@cocotb.test()
async def tc_clordid_and_counter_digits(dut):
    """Hardware ClOrdID with every hex digit and rule 7; the host's ClOrdID as given; UserRefNum near the top."""
    e = await start(dut)
    await e.configure(CFG)
    e.user_ref = 0x89ABCDEF
    e.exchange.user_ref_next = e.user_ref
    await e.send(order(Source.RULE, rule=7))
    await e.send(order(clordid=b"Zz 09 aA      "))
    await e.drain()  # the counter jumps only between frames (as a TX_USERREF_NEXT write, decision 22)
    e.user_ref = USER_REF_MAX
    e.exchange.user_ref_next = e.user_ref
    await e.send(order(Source.RULE, rule=3))
    await e.drain()
    assert [f[76:90] for _, f in e.frames] == [b"HW789ABCDEF   ", b"Zz 09 aA      ", b"HW3FFFFFFFF   "]


@cocotb.test()
async def tc_reset_mid_frame(dut):
    """A reset cuts a frame: nothing more of it, and no config after it."""
    e = await start(dut)
    await e.configure(CFG)
    await e.send(order())
    await e.step()
    e.paused = True
    dut.rst.value = 1
    await e.step()
    await e.step()
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.m_axis_tx_tvalid.value) == 0 and int(dut.st_cfg_valid.value) == 0


# ---------------------------------------------------------------- random


@cocotb.test()
async def tc_random_golden(dut):
    """N_RANDOM random orders under random backpressure and config changes, byte for byte and through the exchange;
    then a fixed phase that hits every bin."""
    cov = Coverage("order_tx_builder", BINS)
    e = await start(dut, cov)
    await every_bin(e)
    ready_p = 1.0
    for n in range(N_RANDOM):
        if n % 100 == 0:
            ready_p = random.choice((1.0, 0.9, 0.5, 0.1))
        o = random_order()
        while True:
            cfg = None
            if random.random() < 0.01:
                cfg = random.choice(list(BAD.values()))
            elif random.random() < 0.01:
                cfg = TxConfig(template(dst_ip=f"10.0.{random.randrange(256)}.{random.randrange(256)}",
                                        dport=random.randrange(65536)),
                               display=random.choice(b"YNA"), capacity=random.choice(b"APRO"))  # fmt: skip
            if await e.step(o if random.random() < 0.8 else None, ready=random.random() < ready_p, cfg=cfg):
                break
    await e.drain()
    dut._log.info(f"{len(e.frames)} frames")
    cov.check(Path.cwd())


async def every_bin(e: Env) -> None:
    for bad in BAD.values():
        await e.step(cfg=bad)  # cfg_overlap
    await e.configure(CFG)
    await e.send(order(Source.RULE, rule=7))  # hw_clordid, rule_7
    await e.drain()
    e.user_ref = 0xABCDEF00
    e.exchange.user_ref_next = e.user_ref
    await e.send(order(Source.RULE, rule=1))  # hex_af
    await e.send(order())  # host_clordid, back_to_back (taken in the last beat's step)
    await e.step(ready=False)
    await e.step(ready=False)  # stall_first
    for _ in range(4):
        await e.step()
    await e.step(ready=False)  # stall_mid
    while e.cur is not None and e.beat < BEATS - 1:
        await e.step()
    await e.step(ready=False)  # stall_last
    await e.drain()
    other = TxConfig(template(dport=6000))
    await e.step(cfg=other)
    await e.step()
    assert await e.step(order())  # cfg_same_edge_as_take: decided at the take edge, the take uses the old one
    await e.step(cfg=CFG)  # cfg_between_frames
    await e.drain()
    await e.send(order(), ready_p=1.0)
    await e.step(ready=False)
    await e.step()  # sent_ts_after_stall
    await e.drain()


# ---------------------------------------------------------------- pytest

LONG_TESTS = ("tc_random_golden",)


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


@pytest.mark.parametrize("group", ["directed", *LONG_TESTS])
def test_order_tx_builder(group):
    run_cocotb(
        toplevel="order_tx_builder",
        filelists=["common.f", "core.f"],
        test_module="test_order_tx_builder",
        testcase=directed_tests() if group == "directed" else group,
        extra_env={"GROUP": group},
    )
