"""Thin wrapper around the cocotb Python runner shared by all tests.

Under pytest, cocotb_tools.runner fails the calling test (SystemExit) when any
cocotb test fails, and forwards pytest's sys.path to the simulator process as
PYTHONPATH, so test modules and `common.*` are importable from cocotb.

Builds are reused: the build directory name is a hash of everything that
affects the compiled model (toplevel, parameters, defines, flags, source
contents, pinned tool versions). A finished build leaves a stamp file and is
not rebuilt. Each distinct set of runtime env vars gets its own run directory,
so one build can be run under several clock ratios or seeds.

Every run uses a seed (HFT_SEED or random), printed for reproduction, and
Verilator randomizes initial values so missing resets show up in tests.
"""

import hashlib
import json
import os
import random
from collections.abc import Iterable
from pathlib import Path

from cocotb_tools.runner import get_runner

from common.filelist import read_filelist

REPO = Path(__file__).resolve().parents[3]
FILELISTS = REPO / "firmware" / "filelists"
BUILD = Path(os.environ.get("HFT_BUILD_DIR") or REPO / "build")
TOOLS_ENV = REPO / "tools.env"

# Randomize uninitialized state: resetless flops start with random values.
X_BUILD_ARGS = ["--x-assign", "unique", "--x-initial", "unique"]
STAMP = ".hft-build-ok"


def run_seed() -> int:
    """Seed for this run: HFT_SEED if set, else random (Verilator needs 1..2**31-1)."""
    env = os.environ.get("HFT_SEED")
    return int(env) if env else random.SystemRandom().randrange(1, 2**31 - 1)


def sources_for(filelists: Iterable[str]) -> list[Path]:
    return [p for f in filelists for p in read_filelist(FILELISTS / f)]


def build_key(name: str, config: object, sources: Iterable[Path]) -> str:
    """Directory name for a build: `<name>_<hash of config + source contents + tools.env>`."""
    h = hashlib.sha1(json.dumps(config, sort_keys=True, default=str).encode())
    for path in [*sources, TOOLS_ENV]:
        h.update(path.read_bytes())
    return f"{name}_{h.hexdigest()[:10]}"


def env_dir_name(env: dict[str, str]) -> str:
    return "run_" + hashlib.sha1(json.dumps(env, sort_keys=True).encode()).hexdigest()[:8]


def run_cocotb(
    *,
    toplevel: str,
    filelists: list[str],
    test_module: str,
    parameters: dict | None = None,
    defines: dict | None = None,
    extra_env: dict | None = None,
    testcase: str | list[str] | None = None,
    sva: bool = True,
) -> None:
    """Build `toplevel` (once per configuration) and run the cocotb `test_module`.

    parameters: HDL parameters (part of the build key).
    defines:    preprocessor defines, e.g. {"HFT_SIM_CDC_JITTER": 1} (part of the build key).
    extra_env:  runtime-only settings such as RATIO (not part of the build key).
    sva:        also compile filelists/sva.f (assertion modules and binds).
    """
    parameters = dict(parameters or {})
    defines = dict(defines or {})
    env = {k: str(v) for k, v in (extra_env or {}).items()}
    waves = os.environ.get("WAVES", "0") == "1"
    seed = run_seed()

    lists = [*filelists, *(["sva.f"] if sva else [])]
    sources = sources_for(lists)
    build_args = ["-Wall", "--assert", *X_BUILD_ARGS]
    if waves:
        build_args += ["--trace-fst", "--trace-structs"]

    config = {"top": toplevel, "params": parameters, "defines": defines, "args": build_args}
    build_dir = BUILD / "sim" / build_key(toplevel, config, sources)
    run_dir = build_dir / env_dir_name(env)
    run_dir.mkdir(parents=True, exist_ok=True)

    runner = get_runner("verilator")
    stamp = build_dir / STAMP
    if not stamp.exists():
        runner.build(
            sources=sources,
            hdl_toplevel=toplevel,
            parameters=parameters,
            defines=defines,
            build_args=build_args,
            build_dir=build_dir,
            waves=waves,
            timescale=("1ns", "1ps"),
        )
        stamp.write_text(json.dumps(config, sort_keys=True, default=str))

    print(f"[hft] {toplevel} seed={seed} build={build_dir.name} env={env}  (rerun: HFT_SEED={seed})")
    runner.test(
        hdl_toplevel=toplevel,
        hdl_toplevel_lang="verilog",  # required when build() was skipped (reused build)
        test_module=test_module,
        testcase=testcase,
        build_dir=build_dir,
        test_dir=run_dir,
        results_xml=str(run_dir / "results.xml"),
        seed=seed,
        plusargs=["+verilator+rand+reset+2", f"+verilator+seed+{seed}"],
        extra_env=env,
        waves=waves,
    )
