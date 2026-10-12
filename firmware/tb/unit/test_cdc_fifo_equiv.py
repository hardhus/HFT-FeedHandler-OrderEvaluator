"""cdc_async_fifo, portable implementation, under the cross-simulator equivalence TB.

The same TB and seeds run the XPM implementation in Vivado XSIM (`just xsim-test xpm <seed>`),
so both implementations are checked against the same contract and stimulus.
"""

import pytest

from common.svtb import run_svtb

SEEDS = [1, 2, 3]


@pytest.mark.parametrize("seed", SEEDS)
def test_cdc_fifo_equiv_portable(seed):
    run_svtb(
        top="tb_cdc_fifo_equiv",
        filelists=["common.f", "sva.f"],  # fifo_props checks the contract
        tb_files=["firmware/tb/sv/tb_cdc_fifo_equiv.sv"],
        seed=seed,
    )
