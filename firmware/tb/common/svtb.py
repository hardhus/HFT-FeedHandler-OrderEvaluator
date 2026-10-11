"""Run a self-checking SystemVerilog testbench under Verilator (--binary --timing).

Used for testbenches that must also run in other simulators (e.g. XSIM), where
cocotb is not available. The TB must print exactly one result line:

    HFT_TB_RESULT: PASS ...    or    HFT_TB_RESULT: FAIL ...

The build is reused the same way as in runner.py.
"""

import subprocess
from pathlib import Path

from common.runner import BUILD, REPO, STAMP, X_BUILD_ARGS, build_key, run_seed, sources_for

SENTINEL = "HFT_TB_RESULT:"


def run_svtb(
    *,
    top: str,
    filelists: list[str],
    tb_files: list[str | Path],
    defines: dict | None = None,
    plusargs: list[str] | None = None,
    timeout_s: int = 600,
) -> str:
    """Compile and run `top`; fail unless the TB prints a PASS sentinel. Returns the output."""
    defines = dict(defines or {})
    tb_paths = [(REPO / p).resolve() if not Path(p).is_absolute() else Path(p) for p in tb_files]
    sources = [*sources_for(filelists), *tb_paths]
    args = ["--binary", "--timing", "--assert", "--timescale", "1ns/1ps", "-Wno-fatal", *X_BUILD_ARGS]
    config = {"top": top, "defines": defines, "args": args}
    build_dir = BUILD / "svtb" / build_key(top, config, sources)
    exe = build_dir / f"V{top}"

    if not (build_dir / STAMP).exists():
        build_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "verilator", *args, "-j", "0",
            "--top-module", top, "-Mdir", str(build_dir), "-o", exe.name,
            *[f"+define+{k}={v}" for k, v in defines.items()],
            *map(str, sources),
        ]  # fmt: skip
        subprocess.run(cmd, check=True, cwd=REPO)
        (build_dir / STAMP).write_text(" ".join(cmd))

    seed = run_seed()
    run = [str(exe), f"+seed={seed}", "+verilator+rand+reset+2", f"+verilator+seed+{seed}"]
    run += plusargs or []
    print(f"[hft] {top} seed={seed} build={build_dir.name}  (rerun: HFT_SEED={seed})")
    proc = subprocess.run(run, capture_output=True, text=True, timeout=timeout_s, cwd=build_dir)
    out = proc.stdout + proc.stderr
    print(out)

    results = [line for line in out.splitlines() if SENTINEL in line]
    assert proc.returncode == 0, f"{top}: simulator exited with {proc.returncode}"
    assert len(results) == 1, f"{top}: expected one '{SENTINEL}' line, got {len(results)}"
    assert f"{SENTINEL} PASS" in results[0], f"{top}: {results[0].strip()}"
    return out
