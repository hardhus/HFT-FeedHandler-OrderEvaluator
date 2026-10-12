"""Tests of the SVA checkers themselves (they must fire on violations and stay quiet otherwise)."""

import pytest

from common.svtb import run_svtb

TB = ["firmware/tb/sva/fifo_props.sv", "firmware/tb/sv/tb_fifo_props_arming.sv"]


def test_fifo_props_ignores_power_up_busy_glitch():
    """Regression (S6): a stale rd_rst_busy glitch during the power-up rst hold must not be
    taken for the end of reset."""
    run_svtb(top="tb_fifo_props_arming", filelists=[], tb_files=TB)


def test_fifo_props_flags_non_empty_after_reset():
    """The real end of reset with a non-empty FIFO must fire a_empty_after_reset."""
    with pytest.raises(AssertionError, match="exited with|expected one"):
        run_svtb(top="tb_fifo_props_arming", filelists=[], tb_files=TB, defines={"HFT_VIOLATE": 1})
