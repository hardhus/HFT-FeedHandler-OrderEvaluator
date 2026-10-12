"""axis_register and axis_skid_buffer: same AXI-Stream contract, different ready path.

Both: 1-cycle latency, 1 beat/cycle, no loss/duplication/reordering, payload stable
while stalled. Only axis_skid_buffer has a registered s_axis_tready; for
axis_register the test checks that the combinational path is really there, which
also shows that tc_tready_path can detect it.
"""

import itertools
import os
import random

import cocotb
import pytest
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotbext.axi import AxiStreamBus, AxiStreamFrame, AxiStreamSink, AxiStreamSource

from common.clocks import start_clock
from common.runner import run_cocotb

PERIOD_PS = 6400
# capacity while the output is stalled: output register (+ skid register)
CAPACITY = {"axis_register": 1, "axis_skid_buffer": 2}


def cfg() -> tuple[str, int, int]:
    return os.environ["KIND"], int(os.environ["DATA_W"]), int(os.environ["USER_W"])


async def start(dut, cycles: int = 4) -> None:
    """Idle inputs and reset first, then the clock (no edge with undriven inputs)."""
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tkeep.value = 0
    dut.s_axis_tlast.value = 0
    dut.s_axis_tuser.value = 0
    dut.m_axis_tready.value = 0
    dut.rst.value = 1
    start_clock(dut.clk, PERIOD_PS)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def beat(i: int, data_w: int, user_w: int) -> dict[str, int]:
    """A distinct, recognizable beat for index i."""
    rng = random.Random(i)
    return {
        "tdata": rng.getrandbits(data_w),
        "tkeep": rng.getrandbits(data_w // 8) | 1,
        "tlast": rng.getrandbits(1),
        "tuser": rng.getrandbits(user_w),
    }


def drive(dut, b: dict[str, int] | None) -> None:
    dut.s_axis_tvalid.value = int(b is not None)
    if b is not None:
        for k, v in b.items():
            getattr(dut, f"s_axis_{k}").value = v


def sample_out(dut) -> dict[str, int]:
    return {k: int(getattr(dut, f"m_axis_{k}").value) for k in ("tdata", "tkeep", "tlast", "tuser")}


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_random_frames(dut):
    """Frames through cocotbext-axi with random gaps on both sides: payload, tkeep, tlast and
    tuser arrive intact and in order."""
    _, data_w, user_w = cfg()
    await start(dut)
    bus_in = AxiStreamBus.from_prefix(dut, "s_axis")
    bus_out = AxiStreamBus.from_prefix(dut, "m_axis")
    src = AxiStreamSource(bus_in, dut.clk, dut.rst)
    snk = AxiStreamSink(bus_out, dut.clk, dut.rst)
    src.set_pause_generator(itertools.cycle([random.random() < 0.3 for _ in range(101)]))
    snk.set_pause_generator(itertools.cycle([random.random() < 0.4 for _ in range(97)]))

    frames = []
    for _ in range(300):
        payload = bytes(random.getrandbits(8) for _ in range(random.randint(1, 4 * data_w // 8 + 3)))
        frames.append((payload, random.getrandbits(user_w)))
    for payload, user in frames:
        await src.send(AxiStreamFrame(payload, tuser=user))

    for n, (payload, user) in enumerate(frames):
        rx = await snk.recv()
        assert bytes(rx.tdata) == payload, f"frame {n}: payload mismatch"
        users = rx.tuser if isinstance(rx.tuser, list) else [rx.tuser]
        assert all(u == user for u in users), f"frame {n}: tuser {users} != {user}"
    assert snk.empty(), "extra frames at the output"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_full_throughput(dut):
    """Continuous tvalid and tready: N beats leave in N consecutive cycles, s_axis_tready stays 1."""
    _, data_w, user_w = cfg()
    await start(dut)
    n = 64
    dut.m_axis_tready.value = 1
    got = []
    for i in range(n + 4):
        await FallingEdge(dut.clk)
        drive(dut, beat(i, data_w, user_w) if i < n else None)
        await ReadOnly()
        if i < n:
            assert dut.s_axis_tready.value == 1, f"cycle {i}: s_axis_tready dropped"
        if dut.m_axis_tvalid.value:
            got.append((i, sample_out(dut)))
    cycles = [c for c, _ in got]
    assert [b for _, b in got] == [beat(i, data_w, user_w) for i in range(n)], "data lost/duplicated/reordered"
    assert cycles == list(range(cycles[0], cycles[0] + n)), "bubble in the output stream"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_stall_hold(dut):
    """While m_axis_tready = 0 the output is stable and exactly CAPACITY beats are accepted;
    after release the accepted beats come out in order."""
    kind, data_w, user_w = cfg()
    await start(dut)
    accepted = []
    first_out = None
    for i in range(20):
        await FallingEdge(dut.clk)
        drive(dut, beat(len(accepted), data_w, user_w))
        await ReadOnly()
        if dut.s_axis_tready.value:
            accepted.append(beat(len(accepted), data_w, user_w))
        if dut.m_axis_tvalid.value:
            out = sample_out(dut)
            first_out = first_out or out
            assert out == first_out, f"cycle {i}: output changed while stalled"
    assert len(accepted) == CAPACITY[kind], f"accepted {len(accepted)} beats while stalled"

    await FallingEdge(dut.clk)
    drive(dut, None)
    dut.m_axis_tready.value = 1
    got = []
    for _ in range(CAPACITY[kind] + 3):
        await ReadOnly()
        if dut.m_axis_tvalid.value:
            got.append(sample_out(dut))
        await FallingEdge(dut.clk)
    assert got == accepted, "beats lost or reordered after stall"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_tready_path(dut):
    """Toggle m_axis_tready in the middle of a cycle (no clock edge) under random traffic and
    see whether s_axis_tready follows in the same time step."""
    kind, data_w, user_w = cfg()
    await start(dut)
    followed = 0
    for i in range(300):
        await FallingEdge(dut.clk)
        drive(dut, beat(i, data_w, user_w) if random.random() < 0.7 else None)
        dut.m_axis_tready.value = random.getrandbits(1)
        await Timer(PERIOD_PS // 8, unit="ps")
        await ReadOnly()
        before = int(dut.s_axis_tready.value)
        await Timer(PERIOD_PS // 8, unit="ps")
        dut.m_axis_tready.value = 1 - int(dut.m_axis_tready.value)
        await ReadOnly()
        followed += int(dut.s_axis_tready.value) != before
    if kind == "axis_skid_buffer":
        assert followed == 0, f"s_axis_tready changed {followed}x without a clock edge (combinational path)"
    else:
        assert followed > 0, "expected axis_register's combinational ready path to be visible"


@cocotb.test(timeout_time=5, timeout_unit="ms")  # a broken DUT must fail, not hang
async def tc_reset_mid_stream(dut):
    """Reset while full and stalled: output empties, input becomes ready, and a beat sent after
    reset is the only one that comes out."""
    _, data_w, user_w = cfg()
    await start(dut)
    for i in range(4):  # fill while stalled
        await FallingEdge(dut.clk)
        drive(dut, beat(100 + i, data_w, user_w))
    await FallingEdge(dut.clk)
    drive(dut, None)
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    await ReadOnly()
    assert dut.m_axis_tvalid.value == 0, "output still valid after reset"
    assert dut.s_axis_tready.value == 1, "input not ready after reset"

    await FallingEdge(dut.clk)
    after = beat(999, data_w, user_w)
    drive(dut, after)
    dut.m_axis_tready.value = 1
    await FallingEdge(dut.clk)
    drive(dut, None)
    got = []
    for _ in range(4):
        await ReadOnly()
        if dut.m_axis_tvalid.value:
            got.append(sample_out(dut))
        await FallingEdge(dut.clk)
    assert got == [after], f"expected only the post-reset beat, got {len(got)} beat(s)"


@pytest.mark.parametrize("data_w,user_w", [(8, 1), (64, 4)], ids=["d8", "d64"])
@pytest.mark.parametrize("kind", ["axis_register", "axis_skid_buffer"])
def test_axis_pipeline(kind, data_w, user_w):
    run_cocotb(
        toplevel=kind,
        filelists=["common.f"],
        test_module="test_axis_pipeline",
        parameters={"DATA_W": data_w, "USER_W": user_w},
        extra_env={"KIND": kind, "DATA_W": data_w, "USER_W": user_w},
    )
