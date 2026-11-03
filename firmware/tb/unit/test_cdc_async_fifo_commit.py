"""cdc_async_fifo COMMIT=1: the reader gets committed words only, in order; rollback, full, reset and the
pointer that crosses to the read side, across clock ratios and with the CDC jitter model. COMMIT=0 is
test_cdc_async_fifo.py, unchanged."""

import random

import cocotb
import pytest
from cocotb.triggers import Combine, FallingEdge, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time
from test_cdc_async_fifo import SYNC_STAGES, WIDTH, depth, jitter, reader, start, wait_not_busy

from common.clocks import PROFILES, profile
from common.runner import run_cocotb
from common.scoreboard import Scoreboard

RUNTIME_PARAMS = ("ratio",)


def words(n: int) -> list[int]:
    return [random.getrandbits(WIDTH) for _ in range(n)]


async def begin(dut):
    dut.wr_commit.value = 0
    dut.wr_rollback.value = 0
    return await start(dut)


async def send(dut, data: list[int], end: str, sb: Scoreboard, same_cycle: bool = False, p: float = 1.0) -> None:
    """Write data, then end the unit: "commit" or "rollback", in the cycle of the last write or the
    next one. Committed words go on the scoreboard before the commit edge."""
    pending = list(data)
    kept: list[int] = []

    def finish() -> None:
        if end == "commit":
            dut.wr_commit.value = 1
            for w in kept:
                sb.push(w)
        else:
            dut.wr_rollback.value = 1

    while pending:
        await FallingEdge(dut.wr_clk)
        if not dut.full.value and not dut.wr_rst_busy.value and random.random() < p:
            dut.wr_en.value = 1
            dut.din.value = pending[0]
            kept.append(pending.pop(0))
            if not pending and same_cycle:
                finish()
        else:
            dut.wr_en.value = 0
    if not (data and same_cycle):
        await FallingEdge(dut.wr_clk)
        dut.wr_en.value = 0
        finish()
    await FallingEdge(dut.wr_clk)
    dut.wr_en.value = 0
    dut.wr_commit.value = 0
    dut.wr_rollback.value = 0


async def stays_empty(dut, cycles: int | None = None) -> None:
    """empty stays high long enough for any word to have crossed (several pointer round trips)."""
    for _ in range(cycles or 8 * (SYNC_STAGES + 4)):
        await RisingEdge(dut.rd_clk)
        await ReadOnly()
        assert dut.empty.value, "an uncommitted word is visible"


async def fill_to_full(dut, limit: int) -> int:
    """Write until full; returns the words accepted."""
    n = 0
    while n < limit:
        await FallingEdge(dut.wr_clk)
        if dut.full.value:
            dut.wr_en.value = 0
            return n
        dut.wr_en.value = 1
        dut.din.value = random.getrandbits(WIDTH)
        n += 1
    raise AssertionError(f"not full after {limit} writes")


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def tc_random_units(dut):
    """Random units, ends, timing and enables: exactly the committed words arrive, once, in order.
    The reader's scoreboard fails on any word that was not committed. Rollback at a full FIFO comes up
    when a unit fills it."""
    pulses = await begin(dut)
    rng = random.Random(random.getrandbits(32))
    sb = Scoreboard("units")
    units = []
    for _ in range(300):
        n = rng.randint(0, depth())
        units.append((words(n), rng.choice(("commit", "commit", "rollback")), rng.random() < 0.5))
    total = sum(len(d) for d, end, _ in units if end == "commit")
    assert total > 0

    async def write() -> None:
        for data, end, same in units:
            await send(dut, data, end, sb, same, rng.choice((1.0, 0.7)))

    await Combine(cocotb.start_soon(write()), cocotb.start_soon(reader(dut, sb, total, 0.7)))
    sb.check_empty()
    assert (pulses.overflow, pulses.underflow) == (0, 0)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_uncommitted_is_not_visible(dut):
    """Written words stay invisible until the commit, however long it takes."""
    await begin(dut)
    data = words(depth() // 2)
    for w in data:
        await FallingEdge(dut.wr_clk)
        dut.wr_en.value = 1
        dut.din.value = w
    await FallingEdge(dut.wr_clk)
    dut.wr_en.value = 0
    await stays_empty(dut)
    sb = Scoreboard("late")
    await send(dut, [], "commit", sb)  # commit with no write of its own
    for w in data:
        sb.push(w)
    await reader(dut, sb, len(data))
    sb.check_empty()


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_commit_and_write_in_one_cycle(dut):
    """A word written in the commit's cycle is kept and visible; a word after the commit is not."""
    await begin(dut)
    sb = Scoreboard("same")
    data = words(4)
    await send(dut, data, "commit", sb, same_cycle=True)
    await reader(dut, sb, len(data))
    sb.check_empty()
    await FallingEdge(dut.wr_clk)
    dut.wr_en.value = 1
    dut.din.value = random.getrandbits(WIDTH)
    await FallingEdge(dut.wr_clk)
    dut.wr_en.value = 0
    await stays_empty(dut)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_rollback_drops_the_unit(dut):
    """A rolled-back unit never appears, with its last word written in the rollback's cycle or before."""
    await begin(dut)
    sb = Scoreboard("rollback")
    for same in (False, True):
        await send(dut, words(5), "rollback", sb, same_cycle=same)
        await stays_empty(dut)
    assert sb.checked == 0


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_rewrite_after_rollback(dut):
    """Slots of a rolled-back unit are written again: only the new words arrive, in order, around the
    pointer wrap too."""
    await begin(dut)
    sb = Scoreboard("rewrite")
    total = 0
    for _ in range(3 * depth() // 5):
        await send(dut, words(5), "rollback", sb)
        data = words(5)
        await send(dut, data, "commit", sb)
        total += len(data)
        await reader(dut, sb, total)
    sb.check_empty()


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_rollback_at_full_frees_the_space(dut):
    """Uncommitted words fill the FIFO (capacity = depth, with nothing visible); a rollback frees it all."""
    pulses = await begin(dut)
    n = await fill_to_full(dut, depth() + 1)
    assert n == depth(), f"capacity {n}, depth {depth()}"
    await stays_empty(dut)
    assert pulses.overflow == 0
    await FallingEdge(dut.wr_clk)
    dut.wr_rollback.value = 1
    await FallingEdge(dut.wr_clk)
    dut.wr_rollback.value = 0
    await FallingEdge(dut.wr_clk)
    assert not dut.full.value, "rollback did not free the space"
    sb = Scoreboard("after")
    data = words(depth())
    await send(dut, data, "commit", sb)
    await reader(dut, sb, depth())
    sb.check_empty()
    assert (pulses.overflow, pulses.underflow) == (0, 0)


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_full_includes_uncommitted_and_unread_words(dut):
    """Committed unread words and uncommitted ones share the space; full is exact over both."""
    await begin(dut)
    sb = Scoreboard("shared")
    data = words(depth() // 2)
    await send(dut, data, "commit", sb)
    n = await fill_to_full(dut, depth())
    assert n == depth() - len(data), f"{n} more words fit after {len(data)} committed ones"
    await FallingEdge(dut.wr_clk)
    dut.wr_rollback.value = 1
    await FallingEdge(dut.wr_clk)
    dut.wr_rollback.value = 0
    await reader(dut, sb, len(data))  # the committed ones survive the rollback
    sb.check_empty()


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_reset_mid_unit(dut):
    """Reset with committed and uncommitted words inside: all gone. A commit or rollback during the
    reset does nothing, and no pre-reset word appears afterwards."""
    await begin(dut)
    sb = Scoreboard("pre")
    await send(dut, words(4), "commit", sb)
    for _ in range(4):
        await FallingEdge(dut.wr_clk)
        dut.wr_en.value = 1
        dut.din.value = random.getrandbits(WIDTH)
    await FallingEdge(dut.wr_clk)
    dut.wr_en.value = 0
    dut.rst.value = 1
    dut.wr_commit.value = 1
    await FallingEdge(dut.wr_clk)
    dut.rst.value = 0
    commit = True
    while dut.wr_rst_busy.value:
        commit = not commit
        dut.wr_commit.value = int(commit)
        dut.wr_rollback.value = int(not commit)
        await FallingEdge(dut.wr_clk)
    dut.wr_commit.value = 0
    dut.wr_rollback.value = 0
    await wait_not_busy(dut)
    await stays_empty(dut)
    await FallingEdge(dut.wr_clk)
    assert not dut.full.value, "full after reset"

    sb = Scoreboard("post")
    data = words(depth())
    await send(dut, data, "commit", sb)
    await reader(dut, sb, depth())
    sb.check_empty()


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def tc_crossing_pointer_moves_one_bit(dut):
    """The pointer that crosses to the read side changes one bit per step, whatever a commit or a
    rollback does to the write pointer, wrap included, and reaches every committed word."""
    await begin(dut)
    steps = 0
    jumps = 0

    async def watch():
        nonlocal steps, jumps
        crossing, write = dut.g_portable.vgray_q, dut.g_portable.wgray_q
        prev, prev_write = int(crossing.value), int(write.value)
        while True:
            await RisingEdge(dut.wr_clk)
            await ReadOnly()
            cur, cur_write = int(crossing.value), int(write.value)
            if cur != prev:
                assert bin(cur ^ prev).count("1") == 1, f"crossing pointer: {prev:#x} -> {cur:#x}"
                steps += 1
            jumps += bin(cur_write ^ prev_write).count("1") > 1  # the rollbacks this test meant to do
            prev, prev_write = cur, cur_write

    cocotb.start_soon(watch())
    sb = Scoreboard("gray")
    total = 0
    for _ in range(4 * depth() // 8):
        data = words(8)
        await send(dut, data, "commit", sb)
        total += len(data)
        # Two words: an even distance in gray code changes at least two bits, so the write pointer jumps.
        await send(dut, words(2), "rollback", sb)
        await reader(dut, sb, total)
    sb.check_empty()
    assert steps == total, f"{steps} steps for {total} committed words"
    assert jumps > 0, "no rollback made the write pointer jump: the test proves nothing"


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def tc_commit_latency(dut):
    """After a commit into an empty FIFO, the first word is visible and the whole unit has crossed to the
    read side within the module header's bounds: 1 and n wr cycles, plus SYNC_STAGES+3 rd cycles. Measured
    at the crossing pointer, so the time the test takes to read does not count."""
    wr_ps, rd_ps, jit = profile()
    slow = 1 + jit / 100  # the longest period of a jittered clock
    await begin(dut)
    sb = Scoreboard("latency")
    total = 0
    n_bits = 2 * depth()

    def limit(n: int) -> float:
        return (n + 1) * wr_ps * slow + (SYNC_STAGES + 4 + jitter()) * rd_ps * slow

    for n in (1, 2, depth() // 2, depth()):
        data = words(n)
        for w in data:
            await FallingEdge(dut.wr_clk)
            dut.wr_en.value = 1
            dut.din.value = w
            sb.push(w)
        await FallingEdge(dut.wr_clk)
        dut.wr_en.value = 0
        dut.wr_commit.value = 1
        await RisingEdge(dut.wr_clk)
        t0 = get_sim_time("ps")
        await FallingEdge(dut.wr_clk)
        dut.wr_commit.value = 0
        total += n
        target = total % n_bits
        target ^= target >> 1
        await ReadOnly()
        while dut.empty.value:
            await RisingEdge(dut.rd_clk)
            await ReadOnly()
        first = get_sim_time("ps") - t0
        assert first <= limit(1), f"first word of {n}: {first} ps, limit {limit(1):.0f}"
        while int(dut.g_portable.wgray_s.value) != target:
            await RisingEdge(dut.rd_clk)
            await ReadOnly()
        whole = get_sim_time("ps") - t0
        assert whole <= limit(n), f"unit of {n}: {whole} ps, limit {limit(n):.0f}"
        await reader(dut, sb, total)
        for _ in range(SYNC_STAGES + 4):
            await RisingEdge(dut.wr_clk)
    sb.check_empty()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def tc_throughput(dut):
    """Both sides at full speed, with commits between small units: at least 95 % of the slower clock's
    word rate, as with COMMIT=0: the walk of the crossing pointer does not slow the path down."""
    wr_ps, rd_ps, _ = profile()
    await begin(dut)
    unit = 4
    n = 100 * unit
    sb = Scoreboard("rate")

    async def write() -> None:
        pending = words(n)
        count = 0
        while pending:
            await FallingEdge(dut.wr_clk)
            dut.wr_commit.value = 0
            if dut.full.value:
                dut.wr_en.value = 0
                continue
            word = pending.pop(0)
            dut.wr_en.value = 1
            dut.din.value = word
            sb.push(word)
            count += 1
            dut.wr_commit.value = int(count % unit == 0)
        await FallingEdge(dut.wr_clk)
        dut.wr_en.value = 0
        dut.wr_commit.value = 0

    t0 = get_sim_time("ps")
    await Combine(cocotb.start_soon(write()), cocotb.start_soon(reader(dut, sb, n)))
    elapsed = get_sim_time("ps") - t0
    rate = n * max(wr_ps, rd_ps) / elapsed
    dut._log.info(f"throughput: depth {depth()}, unit {unit}, {rate:.3f} of the slower side's word rate")
    assert rate >= 0.95, f"throughput {rate:.3f} of the slower side's rate"


# (DEPTH_LOG2, WIDTH, SYNC_STAGES): the minimum depth, the longest round trip the contract covers, and
# the size the ring path uses (1024-bit entries, 256 words).
@pytest.mark.parametrize("ratio", list(PROFILES))
@pytest.mark.parametrize("jitter", [0, 1], ids=["nojit", "jit"])
@pytest.mark.parametrize(
    "depth_log2, width, sync_stages", [(4, 32, 2), (4, 64, 3), (8, 1024, 2)], ids=["d16s2", "d16w64s3", "d256w1024s2"]
)
def test_cdc_async_fifo_commit(depth_log2, width, sync_stages, jitter, ratio):
    run_cocotb(
        toplevel="cdc_async_fifo",
        filelists=["common.f"],
        test_module="test_cdc_async_fifo_commit",
        parameters={"WIDTH": width, "DEPTH_LOG2": depth_log2, "SYNC_STAGES": sync_stages, "COMMIT": 1},
        defines={"HFT_SIM_CDC_JITTER": 1} if jitter else {},
        extra_env={"DEPTH_LOG2": depth_log2, "JITTER": jitter, "RATIO": ratio, "WIDTH": width,
                   "SYNC_STAGES": sync_stages},  # fmt: skip
    )
