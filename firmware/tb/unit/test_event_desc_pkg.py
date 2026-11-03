"""The generated SV pack and stamp functions against hand-written entries (tb_event_desc.sv)."""

from common.svtb import run_svtb


def test_event_desc_pack_functions():
    run_svtb(
        top="tb_event_desc",
        filelists=[],
        tb_files=["firmware/rtl/core/gen/event_desc_pkg.sv", "firmware/tb/sv/tb_event_desc.sv"],
    )
