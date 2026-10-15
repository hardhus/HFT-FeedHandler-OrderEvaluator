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

import fcntl
import hashlib
import json
import os
import random
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from importlib.metadata import version
from contextlib import contextmanager
from pathlib import Path

import cocotb_tools.runner as cocotb_runner
from cocotb_tools.runner import get_runner

from common.filelist import read_filelist

REPO = Path(__file__).resolve().parents[3]
FILELISTS = REPO / "firmware" / "filelists"
BUILD = Path(os.environ.get("HFT_BUILD_DIR") or REPO / "build")
TOOLS_ENV = REPO / "tools.env"

# Randomize uninitialized state: resetless flops start with random values.
X_BUILD_ARGS = ["--x-assign", "unique", "--x-initial", "unique"]
STAMP = ".hft-build-ok"

# SVA filelists that belong to an RTL filelist; added with sva=True (09 §2.10). Keeping them
# out of sva.f means a change to core assertions does not rebuild the common-block tests.
SVA_FOR = {"core.f": "sva_core.f"}


# Rough peak memory of one simulator build job (C++ compiler) plus its share of the
# worker process. Used to keep parallel builds within small machines' RAM.
MB_PER_JOB = 600


# Default cap under WSL: its MemAvailable is not backed by free host RAM, and when the
# Windows host cannot supply memory the whole WSL VM crashes (Wsl/Service/E_UNEXPECTED).
WSL_DEFAULT_JOBS = 2


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        return False


def job_budget() -> int:
    """Machine-wide parallelism (pytest workers and compile jobs together).

    HFT_JOBS overrides it (e.g. in .env.local). Otherwise min(CPUs, available
    memory / MB_PER_JOB), capped at WSL_DEFAULT_JOBS under WSL. At least 1.
    """
    if os.environ.get("HFT_JOBS"):
        return max(1, int(os.environ["HFT_JOBS"]))
    budget = os.cpu_count() or 1
    try:
        with open("/proc/meminfo") as f:
            avail_kb = next(int(line.split()[1]) for line in f if line.startswith("MemAvailable:"))
        budget = min(budget, avail_kb // 1024 // MB_PER_JOB)
    except (OSError, StopIteration):
        pass
    if is_wsl():
        budget = min(budget, WSL_DEFAULT_JOBS)
    return max(1, budget)


def build_jobs() -> int:
    """Compile jobs for this process: the budget shared by all pytest-xdist workers.

    Without this every worker runs `make -j<all CPUs>`, and WSL (4 GB), the
    Raspberry Pi or CI runners run out of memory (the OOM killer ends cc1plus).
    """
    workers = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
    return max(1, job_budget() // workers)


@contextmanager
def build_lock(build_dir: Path) -> Iterator[None]:
    """Exclusive lock per build directory, so concurrent workers never compile the same
    build at once (the second one waits, then finds the stamp and reuses the build)."""
    build_dir.mkdir(parents=True, exist_ok=True)
    with open(build_dir / ".hft-build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_seed() -> int:
    """Seed for this run: HFT_SEED if set, else random (Verilator needs 1..2**31-1)."""
    env = os.environ.get("HFT_SEED")
    return int(env) if env else random.SystemRandom().randrange(1, 2**31 - 1)


def sources_for(filelists: Iterable[str]) -> list[Path]:
    return [p for f in filelists for p in read_filelist(FILELISTS / f)]


def sim_filelists(filelists: list[str], sva: bool) -> list[str]:
    """Filelists of a build: the RTL lists, then (sva=True) sva.f and each list's SVA_FOR partner."""
    if not sva:
        return list(filelists)
    return [*filelists, "sva.f", *(SVA_FOR[f] for f in filelists if f in SVA_FOR)]


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
    sva:        also compile filelists/sva.f and the SVA_FOR lists (assertion modules and binds).
    """
    parameters = dict(parameters or {})
    defines = dict(defines or {})
    env = {k: str(v) for k, v in (extra_env or {}).items()}
    waves = os.environ.get("WAVES", "0") == "1"
    seed = run_seed()

    sources = sources_for(sim_filelists(filelists, sva))
    build_args = ["-Wall", "--assert", *X_BUILD_ARGS]
    if waves:
        build_args += ["--trace-fst", "--trace-structs"]

    # The model links cocotb's libraries by path: a different venv/cocotb means a new build.
    config = {"top": toplevel, "params": parameters, "defines": defines, "args": build_args,
              "cocotb": [cocotb_runner.__file__, version("cocotb")]}  # fmt: skip
    build_dir = BUILD / "sim" / build_key(toplevel, config, sources)
    run_dir = build_dir / env_dir_name(env)
    run_dir.mkdir(parents=True, exist_ok=True)

    cocotb_runner.MAX_PARALLEL_BUILD_JOBS = build_jobs()  # read by the runner's `make -j`
    runner = get_runner("verilator")
    stamp = build_dir / STAMP
    with build_lock(build_dir):
        if not stamp.exists():
            build_log = build_dir / "build.log"
            # cocotb puts this interpreter's sys.path into the build's PYTHONPATH, while
            # Verilator's makefile runs its helper scripts (verilator_includer) with the system
            # `python3`. A different Python version then imports this stdlib and dies
            # ("SRE module mismatch"). Make the helpers use this interpreter: variable
            # assignments in MAKEFLAGS act like make command-line overrides.
            makeflags = os.environ.get("MAKEFLAGS")
            os.environ["MAKEFLAGS"] = f"{makeflags or ''} -- PYTHON3={sys.executable}".strip()
            try:
                runner.build(
                    sources=sources,
                    hdl_toplevel=toplevel,
                    parameters=parameters,
                    defines=defines,
                    build_args=build_args,
                    build_dir=build_dir,
                    waves=waves,
                    timescale=("1ns", "1ps"),
                    log_file=build_log,
                )
            except (SystemExit, RuntimeError) as exc:
                raise AssertionError(
                    f"{toplevel} build failed: config={config}\nlog: {build_log}\n{failure_lines(build_log)}"
                ) from exc
            finally:
                if makeflags is None:
                    os.environ.pop("MAKEFLAGS", None)
                else:
                    os.environ["MAKEFLAGS"] = makeflags
            stamp.write_text(json.dumps(config, sort_keys=True, default=str))

    log = run_dir / "sim.log"
    print(f"[hft] {toplevel} seed={seed} build={build_dir.name} env={env}  (rerun: HFT_SEED={seed})")
    try:
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
            log_file=log,
        )
    except (SystemExit, RuntimeError) as exc:
        # Make every failure reproducible from the report alone: seed, env, log and the
        # simulator's own error lines (the log stays in the run directory).
        raise AssertionError(
            f"{toplevel} failed: seed={seed} env={env}\n"
            f"rerun: HFT_SEED={seed} (same parameters/env)\nlog: {log}\n{failure_lines(log)}"
        ) from exc
    problems = run_problems(run_dir / "results.xml", log, testcase)
    if problems:
        raise AssertionError(
            f"{toplevel}: the simulation passed, but {'; '.join(problems)}\nseed={seed} env={env}\nlog: {log}"
        )


# A sim log line with one of these means an assertion or the simulator reported an error.
ERROR_MARKERS = ("Assertion failed", "%Error")


def run_problems(results_xml: Path, log: Path, testcase: str | list[str] | None) -> list[str]:
    """Reasons a passing cocotb run must still fail. Each catches a way a test can count as a
    pass without having checked anything:
    - no test ran (e.g. a `testcase` filter that matches nothing makes cocotb run zero tests);
    - a requested test did not run;
    - the log reports an assertion or simulator error that did not stop the run."""
    try:
        ran = [tc.get("name") for tc in ET.parse(results_xml).getroot().iter("testcase")]
    except (OSError, ET.ParseError) as exc:
        return [f"no readable {results_xml.name} ({exc})"]
    problems = []
    if not ran:
        problems.append("no cocotb test ran")
    wanted = [testcase] if isinstance(testcase, str) else list(testcase or [])
    missing = [name for name in wanted if name not in ran]
    if missing:
        problems.append(f"requested test(s) did not run: {missing}")
    try:
        errors = [line.strip() for line in log.read_text(errors="replace").splitlines()
                  if any(m in line for m in ERROR_MARKERS)]  # fmt: skip
    except OSError:
        errors = []
    if errors:
        problems.append(f"the log reports errors: {errors[:3]}")
    return problems


def failure_lines(log: Path, limit: int | None = None) -> str:
    """The interesting lines of a simulation log, in log order: every failed-test line of the
    cocotb regression (the list of failed tests is always complete), plus at most `limit`
    assertion/error lines (default HFT_LOG_LINES, else 12). Without any, the log's tail."""
    if limit is None:
        limit = int(os.environ.get("HFT_LOG_LINES") or 12)
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return "(no log)"
    keys = ("Assertion failed", "AssertionError", "%Error", "%Warning", "error:", "Error:", " failed", "FAIL", "Killed")
    out, details = [], 0
    for line in lines:
        if "cocotb.regression" in line and " failed" in line:
            out.append(line.strip())
        elif details < limit and any(k in line for k in keys):
            out.append(line.strip())
            details += 1
    return "\n".join(out) or "\n".join(lines[-limit:])
