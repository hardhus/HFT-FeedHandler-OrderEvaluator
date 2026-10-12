"""sync_bit: STAGES-cycle latency, stability, gray-bus validity and the CDC jitter model.

The source side has no clock port: the testbench changes `d` on its own clock
(period and jitter from the RATIO profile), asynchronously to `clk`.
"""

import os
import random

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer

from common.clocks import PROFILES, profile, start_clock
from common.runner import run_cocotb

# Settings passed via env only; they do not change the build.
RUNTIME_PARAMS = ("ratio",)


def cfg() -> tuple[int, int, bool]:
    return int(os.environ["WIDTH"]), int(os.environ["STAGES"]), os.environ["JITTER"] == "1"


def gray(n: int) -> int:
    return n ^ (n >> 1)


def ungray(g: int) -> int:
    n = 0
    while g:
        n ^= g
        g >>= 1
    return n


async def start(dut):
    """Start the destination clock and let the (resetless) chain settle on d = 0."""
    width, stages, _ = cfg()
    _, dst_ps, jit = profile()
    dut.d.value = 0
    start_clock(dut.clk, dst_ps, jit)
    for _ in range(stages + 2):
        await RisingEdge(dut.clk)


async def edges_until(dut, expected: int, limit: int) -> int:
    """Count rising edges until q == expected (sampled in ReadOnly after each edge)."""
    for n in range(1, limit + 1):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.q.value) == expected:
            return n
    raise AssertionError(f"q never became {expected:#x} within {limit} edges")


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_latency(dut):
    """Single-bit change -> q follows after exactly STAGES edges (STAGES or STAGES+1 with jitter)."""
    width, stages, jitter = cfg()
    await start(dut)
    value, seen = 0, set()
    for _ in range(50):
        await FallingEdge(dut.clk)
        value ^= 1 << random.randrange(width)
        dut.d.value = value
        n = await edges_until(dut, value, stages + 3)
        seen.add(n)
    allowed = {stages, stages + 1} if jitter else {stages}
    assert seen <= allowed, f"latencies {sorted(seen)} not within {sorted(allowed)}"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_stable_input(dut):
    """With a constant input, q equals it and never glitches."""
    width, stages, _ = cfg()
    await start(dut)
    value = random.getrandbits(width)
    dut.d.value = value
    for _ in range(stages + 2):
        await RisingEdge(dut.clk)
    for _ in range(1000):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.q.value) == value


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_gray_bus_valid(dut):
    """A gray counter clocked by the source clock: every sampled q is a code the source
    actually produced, sampled codes never go backwards, and q catches up at the end."""
    width, stages, _ = cfg()
    if width < 4:
        return  # a 1-bit "gray counter" wraps every step; the check needs a real bus
    src_ps, _, jit = profile()
    mask = (1 << width) - 1
    await start(dut)
    state = {"count": 0, "run": True}

    async def source():
        rng = random.Random(random.getrandbits(32))
        span = src_ps * jit // 100
        while state["run"]:
            await Timer(src_ps + rng.randint(-span, span), unit="ps")
            state["count"] += 1
            dut.d.value = gray(state["count"] & mask)

    src = cocotb.start_soon(source())
    prev = 0  # binary index of the last sampled code (start value 0)
    for _ in range(3000):
        await RisingEdge(dut.clk)
        await ReadOnly()
        idx = ungray(int(dut.q.value))
        advanced = (idx - prev) & mask  # how far q moved since the last sample
        behind = (state["count"] - prev) & mask  # how far the source moved
        assert advanced <= behind, (
            f"invalid or backwards code: q index {idx}, previous {prev}, source count {state['count'] & mask}"
        )
        prev = idx
    state["run"] = False
    src.cancel()
    for _ in range(stages + 2):
        await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.q.value) == gray(state["count"] & mask), "q did not settle to the final code"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_min_pulse(dut):
    """Pulse-width contract. A level held longer than two clk periods (the longest period,
    jitter included) always shows up on q. A pulse that spans a single clk edge is not
    covered: the jitter model (like real metastability) may miss it."""
    width, stages, jitter = cfg()
    _, dst_ps, jit = profile()
    await start(dut)
    max_period = dst_ps * (100 + jit) // 100

    async def seen_within(bit: int, edges: int) -> bool:
        for _ in range(edges):
            await RisingEdge(dut.clk)
            await ReadOnly()
            if int(dut.q.value) & bit:
                return True
        return False

    async def settle():
        for _ in range(stages + 3):
            await RisingEdge(dut.clk)

    # Contract: > 2 longest periods at a random phase -> never missed.
    for _ in range(40):
        bit = 1 << random.randrange(width)
        await FallingEdge(dut.clk)
        await Timer(random.randint(0, dst_ps), unit="ps")
        watch = cocotb.start_soon(seen_within(bit, stages + 5))
        dut.d.value = bit
        await Timer(2 * max_period + 1, unit="ps")
        dut.d.value = 0
        assert await watch, f"pulse of {2 * max_period + 1} ps on bit {bit:#x} never reached q"
        await settle()

    # Outside the contract: a pulse that spans exactly one rising edge. The jitter model drops
    # it when the rising change is captured late and the falling one is not (p = 1/4), so
    # 120 tries miss nothing with p = 0.75**120 ~ 1e-15.
    missed = 0
    for _ in range(120):
        bit = 1 << random.randrange(width)
        await FallingEdge(dut.clk)
        watch = cocotb.start_soon(seen_within(bit, stages + 3))
        dut.d.value = bit
        await FallingEdge(dut.clk)
        dut.d.value = 0
        missed += not await watch
        await settle()
    if jitter:
        assert missed > 0, "jitter model never dropped a one-edge pulse"
    else:
        assert missed == 0, f"{missed} one-edge pulses dropped without the jitter model"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_jitter_model(dut):
    """All bits flip at once: with HFT_SIM_CDC_JITTER some samples show a mixture of old and
    new bits (the model is active); without it q only ever shows the old or the new word."""
    width, stages, jitter = cfg()
    if width == 1:
        return
    await start(dut)
    mask = (1 << width) - 1
    value, mixed = 0, 0
    for _ in range(64):
        await FallingEdge(dut.clk)
        old, value = value, value ^ mask
        dut.d.value = value
        for _ in range(stages + 2):
            await RisingEdge(dut.clk)
            await ReadOnly()
            q = int(dut.q.value)
            mixed += q not in (old, value)
    if jitter:
        assert mixed > 0, "jitter model never produced a mixed word"
    else:
        assert mixed == 0, f"{mixed} mixed words without the jitter model"


@pytest.mark.parametrize("ratio", list(PROFILES))
@pytest.mark.parametrize("jitter", [0, 1], ids=["nojit", "jit"])
@pytest.mark.parametrize("width,stages", [(1, 2), (8, 3)], ids=["w1s2", "w8s3"])
def test_sync_bit(width, stages, jitter, ratio):
    run_cocotb(
        toplevel="sync_bit",
        filelists=["common.f"],
        test_module="test_sync_bit",
        parameters={"WIDTH": width, "STAGES": stages},
        defines={"HFT_SIM_CDC_JITTER": 1} if jitter else {},
        extra_env={"WIDTH": width, "STAGES": stages, "JITTER": jitter, "RATIO": ratio},
    )
