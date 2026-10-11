"""Thin wrapper around the cocotb Python runner shared by all tests.

Under pytest, cocotb_tools.runner fails the calling test (SystemExit) when any
cocotb test fails, and forwards pytest's sys.path to the simulator process as
PYTHONPATH, so test modules and `common.*` are importable from cocotb.
"""

import hashlib
import json
import os
from pathlib import Path

from cocotb_tools.runner import get_runner

from common.filelist import read_filelist

REPO = Path(__file__).resolve().parents[3]
FILELISTS = REPO / "firmware" / "filelists"
BUILD = Path(os.environ.get("HFT_BUILD_DIR") or REPO / "build")


def run_cocotb(
    *,
    toplevel: str,
    filelists: list[str],
    test_module: str,
    parameters: dict | None = None,
    extra_env: dict | None = None,
) -> None:
    """Build `toplevel` from the given filelists and run the cocotb `test_module`.

    Each parameter set gets its own build dir so pytest-xdist workers never
    share a Verilator object directory.
    """
    parameters = parameters or {}
    sim = os.environ.get("SIM", "verilator")
    waves = os.environ.get("WAVES", "0") == "1"
    tag = hashlib.sha1(json.dumps(parameters, sort_keys=True).encode()).hexdigest()[:8]
    build_dir = BUILD / "sim" / f"{toplevel}_{tag}"
    sources = [p for f in filelists for p in read_filelist(FILELISTS / f)]

    build_args: list[str] = []
    if sim == "verilator":
        build_args += ["-Wall", "--assert"]
        if waves:
            build_args += ["--trace-fst", "--trace-structs"]

    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel=toplevel,
        parameters=parameters,
        build_args=build_args,
        build_dir=build_dir,
        always=True,
        waves=waves,
        timescale=("1ns", "1ps"),
    )
    runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        build_dir=build_dir,
        test_dir=build_dir,
        results_xml=str(build_dir / "results.xml"),
        extra_env={k: str(v) for k, v in (extra_env or {}).items()},
        waves=waves,
    )
