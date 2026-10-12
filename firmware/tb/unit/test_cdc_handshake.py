"""cdc_handshake: each accepted word arrives exactly once, intact and in order, across clock ratios
and with the CDC jitter model."""

import os
import random

import cocotb
import pytest
from cocotb.triggers import Combine, FallingEdge, ReadOnly, RisingEdge

from common.clocks import PROFILES, profile, start_clock
from common.runner import run_cocotb
from common.scoreboard import Scoreboard

RUNTIME_PARAMS = ("ratio",)
WIDTH = 64
STAGES = 2


def jitter() -> int:
    return int(os.environ["JITTER"])


async def start(dut) -> None:
    """Both clocks, both resets asserted together, released in their own domains."""
    src_ps, dst_ps, jit = profile()
    dut.src_valid.value = 0
    dut.src_data.value = 0
    dut.src_rst.value = 1
    dut.dst_rst.value = 1
    start_clock(dut.src_clk, src_ps, jit)
    start_clock(dut.dst_clk, dst_ps, jit)
    for _ in range(STAGES + 4):
        await RisingEdge(dut.src_clk)
        await RisingEdge(dut.dst_clk)

    async def release(clk, rst):
        await FallingEdge(clk)
        rst.value = 0

    await Combine(cocotb.start_soon(release(dut.src_clk, dut.src_rst)), cocotb.start_soon(release(dut.dst_clk, dut.dst_rst)))
    for _ in range(STAGES + 2):
        await RisingEdge(dut.src_clk)


async def sender(dut, words: list[int], sb: Scoreboard, p_valid: float = 1.0) -> None:
    """Offer words; push each to the scoreboard only when it is accepted (valid && ready at
    the next src edge). src_ready is registered, so its value at the falling edge holds."""
    pending = list(words)
    while pending:
        await FallingEdge(dut.src_clk)
        if dut.src_ready.value and random.random() < p_valid:
            dut.src_valid.value = 1
            dut.src_data.value = pending[0]
            sb.push(pending.pop(0))
        else:
            dut.src_valid.value = 0
    await FallingEdge(dut.src_clk)
    dut.src_valid.value = 0


async def receiver(dut, sb: Scoreboard, count: int) -> None:
    """Check every dst_valid pulse against the scoreboard."""
    while sb.checked < count:
        await RisingEdge(dut.dst_clk)
        await ReadOnly()
        if dut.dst_valid.value:
            sb.pop_check(int(dut.dst_data.value))


async def quiet(dut, dst_cycles: int) -> None:
    """No further dst_valid pulses (catches duplicates after the last expected word)."""
    for _ in range(dst_cycles):
        await RisingEdge(dut.dst_clk)
        await ReadOnly()
        assert not dut.dst_valid.value, "unexpected extra dst_valid pulse"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_single_transfer(dut):
    """One word: arrives once, intact, within STAGES+1 dst edges of acceptance (+1 with jitter)."""
    await start(dut)
    word = random.getrandbits(WIDTH)
    await FallingEdge(dut.src_clk)
    assert dut.src_ready.value, "not ready after reset"
    dut.src_valid.value = 1
    dut.src_data.value = word
    await RisingEdge(dut.src_clk)  # accepted here
    edges = 0
    dut.src_valid.value = 0
    while True:
        await RisingEdge(dut.dst_clk)
        edges += 1
        await ReadOnly()
        if dut.dst_valid.value:
            break
        assert edges < 20, "dst_valid never came"
    assert int(dut.dst_data.value) == word
    assert edges <= STAGES + 2 + jitter(), f"latency {edges} dst edges"
    await quiet(dut, 20)


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_back_to_back(dut):
    """The source re-sends as soon as src_ready returns: every word arrives once, in order."""
    await start(dut)
    sb = Scoreboard("handshake")
    words = [random.getrandbits(WIDTH) for _ in range(300)]
    await Combine(cocotb.start_soon(sender(dut, words, sb)), cocotb.start_soon(receiver(dut, sb, len(words))))
    sb.check_empty()
    await quiet(dut, 20)


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_valid_while_busy(dut):
    """src_valid held high with new data every cycle: only words offered while src_ready was
    high are transferred; the rest are ignored."""
    await start(dut)
    sb = Scoreboard("busy")
    total = 0

    async def flood():
        nonlocal total
        for _ in range(1500):
            await FallingEdge(dut.src_clk)
            word = random.getrandbits(WIDTH)
            dut.src_valid.value = 1
            dut.src_data.value = word
            total += 1
            if dut.src_ready.value:
                sb.push(word)
        await FallingEdge(dut.src_clk)
        dut.src_valid.value = 0

    async def collect():
        while True:
            await RisingEdge(dut.dst_clk)
            await ReadOnly()
            if dut.dst_valid.value:
                sb.pop_check(int(dut.dst_data.value))

    rx = cocotb.start_soon(collect())
    await flood()
    for _ in range(40):  # let the last transfer land
        await RisingEdge(dut.dst_clk)
    rx.cancel()
    sb.check_empty()
    assert 0 < sb.checked < total, f"{sb.checked} of {total} offered words transferred"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_data_stable_window(dut):
    """While req_q is high, data_q (the unsynchronized CDC path) never changes."""
    await start(dut)
    sb = Scoreboard("stable")
    words = [random.getrandbits(WIDTH) for _ in range(150)]
    violations = 0

    async def watch():
        nonlocal violations
        held = None
        while True:
            await RisingEdge(dut.src_clk)
            await ReadOnly()
            if dut.req_q.value:
                data = int(dut.data_q.value)
                if held is not None and data != held:
                    violations += 1
                held = data
            else:
                held = None

    w = cocotb.start_soon(watch())
    await Combine(cocotb.start_soon(sender(dut, words, sb, p_valid=0.5)), cocotb.start_soon(receiver(dut, sb, len(words))))
    w.cancel()
    assert violations == 0, f"data_q changed {violations}x while req_q was high"


@pytest.mark.parametrize("ratio", list(PROFILES))
@pytest.mark.parametrize("jitter", [0, 1], ids=["nojit", "jit"])
def test_cdc_handshake(jitter, ratio):
    run_cocotb(
        toplevel="cdc_handshake",
        filelists=["common.f"],
        test_module="test_cdc_handshake",
        parameters={"WIDTH": WIDTH, "STAGES": STAGES},
        defines={"HFT_SIM_CDC_JITTER": 1} if jitter else {},
        extra_env={"JITTER": jitter, "RATIO": ratio},
    )
