"""Smoke test of the run_svtb() flow (SV testbench under Verilator --binary --timing)."""

from common.svtb import run_svtb


def test_reset_sync_svtb():
    run_svtb(
        top="tb_reset_sync_smoke",
        filelists=["common.f"],
        tb_files=["firmware/tb/sv/tb_reset_sync_smoke.sv"],
    )
