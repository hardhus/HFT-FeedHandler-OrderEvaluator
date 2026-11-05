"""event_ring_writer against the ring protocol (tb/model/ring.py, audit/13 §2.7).

A FWFT FIFO model feeds random 128-byte entries, garbage in ring_idx and ctrl included: the writer must
overwrite those and keep the rest. Host memory is cocotbext-axi's AxiRamWrite with random pauses on its channels.
The consumer is the model's RingConsumer, which checks both phases, ctrl and ring_idx; each entry it reads is
then compared, byte by byte, with stamp(entry it was fed, index). The hand test checks the stamp itself.
"""

import logging
import random
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from common.axi_ram import Ram, write_bus
from common.coverage import Coverage
from common.runner import run_cocotb
from model.desc_gen import ENTRY_BYTES
from model.ring import IDX_MOD, PACINGS, RingConsumer, phase_of, slot_addr, stamp

PERIOD_NS = 4
BASE = 0x1234_5678_9ABC_D000
N_LOSSLESS = {512: 1_000_000, 64: 100_000}  # entries (audit/13 §2.10)
MEM_SIZE = 1 << 24


def pause(p: float) -> Iterator[bool]:
    while True:
        yield random.random() < p


class Bench:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.data_w = int(dut.DATA_W.value)
        self.beats = 1024 // self.data_w
        self.fifo: deque[bytes] = deque()  # the FIFO's committed words
        self.popped: deque[bytes] = deque()  # words read by the writer, oldest first
        self.pops = 0
        self.busy = False
        self.mem: Ram | None = None
        self.w_fires: list[int] = []
        self.cycle = 0
        self.record_w = False
        self.on_cycle = None  # called once per cycle, read-only

    async def start(self, p_ready: float = 0.0) -> None:
        dut = self.dut
        dut.rst.value = 1
        dut.fifo_empty.value = 1
        dut.fifo_dout.value = 0
        dut.fifo_rd_rst_busy.value = 0
        dut.cfg_enable.value = 0
        dut.cfg_base.value = 0
        dut.cfg_size_log2.value = 0
        dut.cfg_cons.value = 0
        cocotb.start_soon(Clock(dut.clk, PERIOD_NS, "ns").start())
        self.mem = Ram(write_bus(dut, "m_axi_hmw"), dut.clk, dut.rst, size=MEM_SIZE)
        self.mem.log.setLevel(logging.WARNING)  # it logs one line per burst at INFO: 10^6 lines in the long test
        self.ready(p_ready)
        for _ in range(4):
            await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        dut.rst.value = 0
        cocotb.start_soon(self.feeder())
        await RisingEdge(dut.clk)

    def ready(self, p_pause: float, b_pause: float | None = None) -> None:
        """Random pauses: AW, W and B channels of the memory."""
        for ch, p in (
            (self.mem.aw_channel, p_pause),
            (self.mem.w_channel, p_pause),
            (self.mem.b_channel, p_pause if b_pause is None else b_pause),
        ):
            ch.set_pause_generator(pause(p) if p else None)
            ch.pause = False  # removing a generator keeps its last value

    async def feeder(self) -> None:
        """Drives the FIFO's read side after each falling edge; rd_en is final until the next rising edge."""
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            self.cycle += 1
            empty = not self.fifo
            dut.fifo_empty.value = int(empty)
            dut.fifo_rd_rst_busy.value = int(self.busy)
            if not empty:
                # The garbage in ring_idx and ctrl stays in the word until the writer stamps it.
                dut.fifo_dout.value = int.from_bytes(self.fifo[0], "little")
            await ReadOnly()
            if dut.fifo_rd_en.value:
                assert not empty, "fifo_rd_en on an empty FIFO"
                assert not self.busy, "fifo_rd_en on a FIFO in reset"
                self.popped.append(self.fifo.popleft())
                self.pops += 1
            if self.record_w and dut.m_axi_hmw_wvalid.value and dut.m_axi_hmw_wready.value:
                self.w_fires.append(self.cycle)
            if self.on_cycle:
                self.on_cycle()

    async def enable(self, base: int = BASE, size_log2: int = 4, cons: int = 0) -> None:
        dut = self.dut
        await FallingEdge(dut.clk)
        dut.cfg_base.value = base
        dut.cfg_size_log2.value = size_log2
        dut.cfg_cons.value = cons
        dut.cfg_enable.value = 1
        await RisingEdge(dut.clk)

    async def disable(self) -> None:
        await FallingEdge(self.dut.clk)
        self.dut.cfg_enable.value = 0
        await RisingEdge(self.dut.clk)

    def put(self, n: int = 1) -> list[bytes]:
        out = [random.randbytes(ENTRY_BYTES) for _ in range(n)]
        self.fifo.extend(out)
        return out

    def read(self, addr: int, n: int) -> bytes:
        return self.mem.read(addr % MEM_SIZE, n)

    def consumer(self, base: int = BASE, size_log2: int = 4, update_every: int | None = None) -> RingConsumer:
        def write_cons(idx: int) -> None:
            self.dut.cfg_cons.value = idx

        return RingConsumer(self.read, write_cons, base, size_log2, update_every)

    def set_cons(self, idx: int) -> None:
        self.dut.cfg_cons.value = idx

    def written(self) -> int:
        return int(self.dut.cnt_written.value)

    def bresp_errs(self) -> int:
        return int(self.dut.cnt_bresp_err.value)

    def prod(self) -> int:
        return int(self.dut.prod_idx.value)

    async def wait_written(self, n: int, cycles: int = 20_000) -> None:
        for _ in range(cycles):
            await RisingEdge(self.dut.clk)
            if self.written() + self.bresp_errs() >= n:
                return
        raise AssertionError(f"{self.written()} written, {self.bresp_errs()} errors, wanted {n}")

    async def read_entries(self, cons: RingConsumer, entries: list[bytes], size_log2: int = 4) -> None:
        """Reads every entry through the protocol and compares it with what went in."""
        for e in entries:
            for _ in range(20_000):
                got = cons.poll()
                if got is not None:
                    break
                await RisingEdge(self.dut.clk)
            else:
                raise AssertionError(f"entry {cons.idx} never appeared")
            assert got == stamp(e, cons.idx - 1, size_log2), f"entry {cons.idx - 1}"

    async def idle(self, n: int = 64) -> None:
        await ClockCycles(self.dut.clk, n)


async def bench(dut, p_ready: float = 0.0) -> Bench:
    b = Bench(dut)
    await b.start(p_ready)
    return b


@cocotb.test()
async def tc_hand_stamp(dut):
    """ring_idx and both ctrl bytes against hand-written bytes, whatever came in; the rest unchanged."""
    b = await bench(dut)
    body = bytes(range(16)) + b"\xee" * 4 + bytes(range(100, 139)) + b"\xee" * 4
    body = body[:63] + b"\xee" + bytes(range(150, 213)) + b"\xee"
    await b.enable(size_log2=4)
    b.fifo.extend([body] * 17)  # the 17th is entry 16: first slot again, second pass
    await b.wait_written(16)
    first = b.read(BASE, ENTRY_BYTES)
    want = bytearray(body)
    want[16:20] = bytes.fromhex("00 00 00 00")
    want[63] = 0x81
    want[127] = 0x81
    assert first == bytes(want)
    b.set_cons(16)
    await b.wait_written(17)
    second = b.read(BASE, ENTRY_BYTES)
    want[16:20] = bytes.fromhex("10 00 00 00")
    want[63] = 0x01
    want[127] = 0x01
    assert second == bytes(want)


@cocotb.test()
async def tc_wrap_and_phase(dut):
    """Several passes of the smallest ring with random pauses: address, phase, ring_idx and order."""
    b = await bench(dut, 0.3)
    await b.enable(size_log2=4)
    cons = b.consumer(update_every=1)
    entries = b.put(16 * 5 + 3)
    await b.read_entries(cons, entries)
    assert b.fifo == deque()
    await b.idle()
    assert b.prod() == len(entries)
    assert b.written() == len(entries)
    assert b.bresp_errs() == 0


@cocotb.test()
async def tc_base_address(dut):
    """Entries go to base + slot * 128; base bits 11:0 are ignored."""
    b = await bench(dut)
    base = 0x0000_00AB_CDEF_0000
    aw: list[int] = []

    async def watch() -> None:
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.m_axi_hmw_awvalid.value and dut.m_axi_hmw_awready.value:
                aw.append(int(dut.m_axi_hmw_awaddr.value))

    cocotb.start_soon(watch())
    await b.enable(base=base | 0xFFF, size_log2=4)
    cons = b.consumer(base=base, update_every=1)
    n = 16 + 2
    await b.read_entries(cons, b.put(n))
    await b.idle()
    assert aw == [slot_addr(base, i, 4) for i in range(n)]
    assert int(dut.m_axi_hmw_awlen.value) == b.beats - 1
    assert 8 << int(dut.m_axi_hmw_awsize.value) == b.data_w


@cocotb.test()
async def tc_full_ring_waits(dut):
    """prod - cons == N: nothing is written over an unread entry and the FIFO stays unread (one word may be
    waiting inside). Each entry the host frees lets exactly one more go."""
    b = await bench(dut)
    await b.enable(size_log2=4)
    entries = b.put(16 + 5)
    await b.wait_written(16)
    await b.idle(200)
    assert b.prod() == 16 and b.written() == 16
    assert b.pops <= 17, f"{b.pops} words read from the FIFO with a full ring"
    assert b.read(BASE, ENTRY_BYTES) == stamp(entries[0], 0, 4), "unread entry overwritten"
    for i in range(1, 6):
        b.set_cons(i)
        await b.wait_written(16 + i)
        await b.idle(32)
        assert b.prod() == 16 + i, "more than one entry for one freed slot"
        assert b.read(BASE + (i - 1) * 128, ENTRY_BYTES) == stamp(entries[15 + i], 15 + i, 4)


@cocotb.test()
async def tc_ring_fills_to_n(dut):
    """No slot is kept empty: a ring of N takes N entries, all readable."""
    b = await bench(dut)
    await b.enable(size_log2=5)
    entries = b.put(32)
    await b.wait_written(32)
    await b.idle(32)
    assert b.prod() == 32, "the ring did not fill to N"
    await b.read_entries(b.consumer(size_log2=5, update_every=1), entries, size_log2=5)


@cocotb.test()
async def tc_indexes_wrap_at_2_32(dut):
    """prod and cons are 32-bit wrapping counters: the level, ring_idx and phase stay right across the wrap, on a
    ring that also fills there. Takes the writer to 8 entries before the wrap by depositing its state."""
    b = await bench(dut)
    size_log2 = 4
    start = IDX_MOD - 8
    await b.enable(size_log2=size_log2)
    await b.idle(4)
    await FallingEdge(dut.clk)
    dut.u_ring_props.armed.value = 0  # the jump is not a step of prod: ring_props looks away for it alone
    dut.prod_q.value = start
    dut.addr_q.value = slot_addr(BASE, start, size_log2) >> 7
    dut.phase_q.value = phase_of(start, size_log2)
    dut.cfg_cons.value = start
    await b.idle(4)
    await FallingEdge(dut.clk)
    dut.u_ring_props.armed.value = 1
    cons = RingConsumer(b.read, b.set_cons, BASE, size_log2, update_every=10**9, start=start)
    entries = b.put(16 + 2)
    await b.wait_written(16)
    await b.idle(200)
    assert b.prod() == (start + 16) % IDX_MOD, "the level is wrong across the wrap"
    assert b.written() == 16
    await b.read_entries(cons, entries[:16], size_log2)
    cons.flush()
    await b.read_entries(cons, entries[16:], size_log2)
    assert cons.idx > IDX_MOD


@cocotb.test()
async def tc_enable_rules(dut):
    """Disabled: no read. Rising edge: prod 0, phase 1, new base and size. While enabled, a new base or size has no
    effect. Falling edge: bursts in flight finish and no new one starts; the waiting word is kept, in order."""
    b = await bench(dut)
    b.put(4)
    await b.idle(32)
    assert b.pops == 0, "read while disabled"
    first = list(b.fifo)
    base2 = 0x2000
    await b.enable(size_log2=4)
    await b.wait_written(4)
    dut.cfg_base.value = base2
    dut.cfg_size_log2.value = 6
    more = b.put(2)
    await b.wait_written(6)
    assert b.read(BASE + 4 * 128, ENTRY_BYTES) == stamp(more[0], 4, 4), "base or size changed while enabled"
    assert first[0] != more[0]

    # Slow memory, then disable with bursts on their way.
    b.ready(0.9)
    third = b.put(64)
    await b.idle(40)
    await b.disable()
    prod = b.prod()
    pops = b.pops
    assert prod >= 1
    await b.wait_written(6 + prod - 6)
    await b.idle(2000)
    b.ready(0.0)
    assert b.prod() == prod, "a burst started after disable"
    assert b.pops == pops, "the FIFO read after disable"
    assert b.written() == prod, "a burst in flight did not finish"
    waiting = pops - (prod - 6) - 6  # popped but not started

    # A new enable starts over at slot 0 of the new region, phase 1, with the next entry.
    done = b.written()
    await FallingEdge(dut.clk)
    dut.cfg_cons.value = 0
    await b.enable(base=base2, size_log2=4)
    cons = b.consumer(base=base2, update_every=1)  # checks ring_idx from 0
    rest = third[prod - 6 :]
    assert waiting in (0, 1)
    await b.read_entries(cons, rest)
    await b.wait_written(done + len(rest))


@cocotb.test()
async def tc_enable_with_stale_cons_is_refused(dut):
    """User decision (audit/13 S5): CONS_IDX != 0 on the rising edge refuses the enable, so the host's forgotten write
    is an error and not a silent overwrite. Nothing moves until a disable and an enable with cons 0."""
    b = await bench(dut)
    entries = b.put(3)
    await b.enable(size_log2=4, cons=7)
    await b.idle(64)
    assert dut.status_enable_err.value, "not refused"
    assert b.pops == 0 and b.prod() == 0 and b.written() == 0
    assert not dut.m_axi_hmw_awvalid.value
    # Writing 0 alone does not start it: the rising edge was the check.
    dut.cfg_cons.value = 0
    await b.idle(64)
    assert b.pops == 0, "started without a new enable"
    assert dut.status_enable_err.value, "not sticky"
    await b.disable()
    assert dut.status_enable_err.value
    await b.enable(size_log2=4, cons=0)
    await b.read_entries(b.consumer(update_every=1), entries)
    await b.idle(16)
    assert not dut.status_enable_err.value, "not cleared by an accepted enable"
    # A cons written while running is no enable: it is not refused. A stale one at the next enable is.
    dut.cfg_cons.value = 3
    await b.idle(16)
    assert not dut.status_enable_err.value
    await b.disable()
    prod = b.prod()
    more = b.put(1)
    await b.enable(size_log2=4, cons=3)
    await b.idle(64)
    assert dut.status_enable_err.value
    assert b.prod() == prod, "prod moved on a refused enable"
    assert b.fifo[0] == more[0]


@cocotb.test()
async def tc_enable_with_bad_size_is_refused(dut):
    """RING_SIZE_LOG2 outside 4..20 refuses the enable like a stale cons (audit/13 S6): nothing moves and the writer
    needs a disable and a new enable. The two ends of the range are accepted."""
    b = await bench(dut)
    b.put(1)
    for size_log2 in (0, 3, 21, 31):
        await b.enable(size_log2=size_log2)
        await b.idle(32)
        assert dut.status_enable_err.value, f"size_log2 {size_log2} not refused"
        assert b.pops == 0 and b.prod() == 0 and b.written() == 0
        await b.disable()
    for size_log2 in (20, 4):
        await b.enable(size_log2=size_log2)
        await b.idle(32)
        assert not dut.status_enable_err.value, f"size_log2 {size_log2} refused"
        assert b.prod() == 1, f"size_log2 {size_log2}: prod {b.prod()}"
        await b.disable()
        b.put(1)


@cocotb.test()
async def tc_base_and_size_ignored_while_enabled(dut):
    """A new base or size while enabled has no effect, also where the ring wraps and goes back to its base."""
    b = await bench(dut)
    await b.enable(size_log2=4)
    await b.idle(4)
    dut.cfg_base.value = 0x2000
    dut.cfg_size_log2.value = 6
    await b.read_entries(b.consumer(update_every=1), b.put(16 + 4))
    await b.idle()
    assert b.prod() == 16 + 4


@cocotb.test()
async def tc_word_arriving_as_the_ring_fills_stays_in_the_fifo(dut):
    """The burst that fills the ring empties the waiting register. A word that reaches the FIFO's output in the very
    next cycle is not read: the full flag counts that burst (a_no_pop_when_full checks it too)."""
    b = await bench(dut)
    await b.enable(size_log2=4)
    b.put(16)
    for _ in range(1000):
        await FallingEdge(dut.clk)
        await ReadOnly()
        if b.prod() == 15 and dut.take.value:
            break
    else:
        raise AssertionError(f"never saw the burst that fills the ring, prod {b.prod()}")
    await RisingEdge(dut.clk)
    b.put(1)
    await b.idle(32)
    assert b.prod() == 16
    assert b.pops == 16, "read from the FIFO with a full ring"


@cocotb.test()
async def tc_bresp_error(dut):
    """A burst answered with an error counts and sets the sticky status; no retry and no stop. A new enable clears
    the status, not the counter."""
    b = await bench(dut)
    b.mem.bad_addrs = {BASE + 2 * 128}
    aws = 0

    async def watch() -> None:
        nonlocal aws
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.m_axi_hmw_awvalid.value and dut.m_axi_hmw_awready.value:
                aws += 1

    cocotb.start_soon(watch())
    await b.enable(size_log2=4)
    assert not dut.status_bresp_err.value
    entries = b.put(5)
    await b.wait_written(5)
    await b.idle()
    assert aws == 5, "a retry"
    assert b.written() == 4
    assert b.bresp_errs() == 1
    assert dut.status_bresp_err.value
    assert b.read(BASE + 3 * 128, ENTRY_BYTES) == stamp(entries[3], 3, 4)
    await b.idle(16)
    assert dut.status_bresp_err.value, "not sticky"
    await b.disable()
    assert dut.status_bresp_err.value
    b.mem.bad_addrs = set()
    await b.enable(size_log2=4)
    await b.idle(2)
    assert not dut.status_bresp_err.value
    assert b.bresp_errs() == 1


@cocotb.test()
async def tc_rate_contract(dut):
    """Ring with room, memory always ready: one entry every 1024/DATA_W cycles, no gap in W (audit/13 §2.7). Exactly
    the bound: no idle W cycle among the entries."""
    b = await bench(dut)
    n = 64
    await b.enable(size_log2=8)
    b.put(n)
    b.record_w = True
    await b.wait_written(n, 50_000)
    b.record_w = False
    want = n * b.beats
    assert len(b.w_fires) == want
    span = b.w_fires[-1] - b.w_fires[0] + 1
    assert span == want, f"{want} beats took {span} cycles"


@cocotb.test()
async def tc_slow_answers_limit_outstanding(dut):
    """B held back: no more than MAX_OUTSTANDING bursts start. Each answer lets one more go (ring_props checks the
    bound on every cycle)."""
    b = await bench(dut)
    hold = [True]

    def held() -> Iterator[bool]:
        while True:
            yield hold[0]

    b.mem.b_channel.set_pause_generator(held())
    await b.enable(size_log2=8)
    b.put(10)
    await b.idle(300)
    assert b.prod() == int(dut.MAX_OUTSTANDING.value)
    assert b.written() == 0
    hold[0] = False
    await b.wait_written(10)
    assert b.prod() == 10


@cocotb.test()
async def tc_fifo_in_reset_is_not_read(dut):
    """rd_rst_busy with empty low (the FIFO's reset is on its way): no read, so no stale word goes out."""
    b = await bench(dut)
    b.busy = True
    b.put(3)
    await b.enable(size_log2=4)
    await b.idle(32)
    assert b.pops == 0
    b.busy = False
    await b.wait_written(3)


@cocotb.test()
async def tc_reset_mid_burst(dut):
    """rst in the middle of bursts: all clear, VALIDs low, counters 0, and it runs again from entry 0."""
    b = await bench(dut, 0.5)
    await b.enable(size_log2=4)
    b.put(8)
    await b.idle(10)
    await FallingEdge(dut.clk)
    dut.rst.value = 1
    dut.cfg_enable.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert not dut.m_axi_hmw_awvalid.value and not dut.m_axi_hmw_wvalid.value
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    b.fifo.clear()
    await b.idle(16)
    assert b.prod() == 0 and b.written() == 0 and b.bresp_errs() == 0 and not dut.status_bresp_err.value
    b.ready(0.0)
    b.mem.write(BASE % MEM_SIZE, bytes(16 * ENTRY_BYTES))  # the host rule: a new ring starts at zero
    dut.cfg_cons.value = 0
    await b.enable(size_log2=4)
    cons = b.consumer(update_every=1)
    await b.read_entries(cons, b.put(3))
    await b.idle()
    assert b.written() == 3


@cocotb.test(timeout_time=60, timeout_unit="sec")
async def tc_lossless_random(dut):
    """Entries in order, none lost, through full and empty rings, passes of both phases, every consumer profile and
    random pauses on all channels. The number of entries is the roadmap's (audit/13 §2.10)."""
    n_total = N_LOSSLESS[int(dut.DATA_W.value)]
    b = await bench(dut, 0.1)
    size_log2 = 6
    n = 1 << size_log2
    await b.enable(size_log2=size_log2)
    cons = b.consumer(size_log2=size_log2)
    cov = Coverage(
        f"event_ring_writer_{b.data_w}",
        [f"{state}_phase{p}" for state in ("empty", "half", "full", "wrap") for p in (0, 1)],
    )
    pending: deque[bytes] = deque()
    done = 0

    async def producer() -> None:
        made = 0
        while made < n_total:
            # Bursts that outrun the consumer, and gaps that let it empty the ring.
            room = 2 * n - len(b.fifo)
            if room > 0:
                k = min(room, random.randint(1, n), n_total - made)
                entries = b.put(k)
                pending.extend(entries)
                made += k
            await ClockCycles(dut.clk, random.choice((1, 4 * n)))
            if random.random() < 1 / 8:
                # A narrow bus never catches up by itself: let the host read everything, at any phase.
                while pending:
                    await ClockCycles(dut.clk, 16)

    def sample() -> None:
        """Every cycle, also while the consumer pauses. Full and half as the writer sees them, from the published
        CONS_IDX; empty as the host sees it, with nothing left to read."""
        prod = b.prod()
        level = (prod - int(dut.cfg_cons.value)) % IDX_MOD
        phase = phase_of(prod, size_log2)
        if prod == cons.idx % IDX_MOD:
            cov.hit(f"empty_phase{phase}")
        if level >= n / 2:
            cov.hit(f"half_phase{phase}")
        if level >= n:
            cov.hit(f"full_phase{phase}")
        if prod % n == n - 1:
            cov.hit(f"wrap_phase{phase}")

    b.on_cycle = sample
    cocotb.start_soon(producer())
    while done < n_total:
        # Each 16384 entries: 256 slow, 256 pausing, the rest fast. Slow profiles spend 64 cycles per entry, so
        # any more of them would make the run hours long and add no new ring state.
        pacing = PACINGS[{0: "slow", 1: "pausing"}.get(done % 16384 // 256, "fast")]
        got = cons.poll()
        if got is None:
            await RisingEdge(dut.clk)
            continue
        want = pending.popleft()
        assert got == stamp(want, cons.idx - 1, size_log2), f"entry {cons.idx - 1}"
        done += 1
        if done % (n_total // 10) == 0:
            dut._log.info(f"{done} entries, {cons.torn} torn polls, empty bins: {cov.empty()}")
        wait = pacing.wait_after(done)
        if wait > 1:
            await ClockCycles(dut.clk, wait)
        else:
            await RisingEdge(dut.clk)
    cons.flush()
    await b.idle(100)
    assert b.prod() == n_total
    assert b.written() == n_total
    assert not b.fifo
    assert not dut.status_bresp_err.value
    dut._log.info(f"{n_total} entries, {cons.torn} torn polls")
    cov.check(Path.cwd())


LONG_TESTS = ("tc_lossless_random",)


def directed_tests() -> list[str]:
    return [name for name in globals() if name.startswith("tc_") and name not in LONG_TESTS]


def run(data_w: int, testcase) -> None:
    run_cocotb(
        toplevel="event_ring_writer",
        filelists=["common.f", "core.f"],
        test_module="test_event_ring_writer",
        parameters={"DATA_W": data_w},
        testcase=testcase,
        extra_env={"GROUP": "directed" if isinstance(testcase, list) else testcase},
    )


@pytest.mark.parametrize("data_w", [64, 128, 256, 512])
def test_event_ring_writer(data_w):
    run(data_w, directed_tests())


@pytest.mark.long
@pytest.mark.parametrize("data_w", sorted(N_LOSSLESS))
def test_ring_lossless_ordering(data_w):
    run(data_w, "tc_lossless_random")
