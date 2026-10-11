# Development guide

The project runs in simulation only; no FPGA board is required. Every command goes through
[`just`](https://just.systems). Run `just` with no arguments to list the recipes.

## Environments

| Where | Role | What runs there |
|-------|------|-----------------|
| Windows 11 | Editing, Vivado (optional) | `just <recipe>`: simulation recipes are forwarded to WSL; Vivado recipes run natively |
| WSL 2 (Debian 13) | Lint and simulation | Pinned OSS CAD Suite (Verilator), Verible, uv-managed Python (cocotb, pytest) |
| Raspberry Pi 5 (64-bit Pi OS) | Second Linux target (arm64); later the isolated-core host for C++ | Same flow as WSL |
| Any other x86_64/arm64 Debian or Ubuntu | Same flow as WSL | |

Pinned tool versions live in [`tools.env`](../tools.env), and Python dependencies in `pyproject.toml` and `uv.lock`.
Everything is installed inside the repo (`.tools/`, `.venv/`), and nothing is installed system-wide except a few apt
packages.

## Setup

### Linux, WSL or Raspberry Pi 5

```bash
git clone https://github.com/hardhus/HFT-FeedHandler-OrderEvaluator.git
cd HFT-FeedHandler-OrderEvaluator
./scripts/bootstrap.sh     # apt prerequisites (asks for sudo once), uv, just, pinned tools, Python env
just ci                    # doctor + lint + test
```

`bootstrap.sh` installs uv and just into `~/.local/bin` and then runs `just setup`. It is idempotent: a second run
downloads nothing. The OSS CAD Suite archive is about 700 MB. Unpacking it takes a few minutes, longer on `/mnt/d`
and on the Pi.

The Pi needs the **64-bit** OS (`uname -m` must print `aarch64`).

### Windows

Requirements: WSL 2 with a Debian distro, plus [just](https://just.systems) and Git on Windows.

```powershell
just setup      # runs ./scripts/bootstrap.sh inside WSL
just ci
```

Optional per-machine settings go in `.env.local` (git-ignored). Copy them from [`.env.example`](../.env.example):

| Variable | Meaning |
|----------|---------|
| `VIVADO` | Path to `vivado.bat` if it is not on `PATH` |
| `HFT_WSL_DISTRO` | WSL distro used by forwarded recipes (default `Debian`) |
| `HFT_BUILD_DIR` | Absolute build output directory (default `<repo>/build`) |

## Daily flow

```bash
just lint                 # Verilator -Wall + Verible lint + format check (blocking)
just fmt                  # format new RTL in place
just test                 # all cocotb tests, in parallel
just test-one reset_sync  # tests matching a name (pytest -k)
just waves reset_sync     # re-run with FST tracing and print the .fst paths
just lint-legacy          # lint report for the legacy RTL -> build/lint/legacy.log
```

Build outputs go to `build/`: `build/sim/<top>_<hash>/` per parameter set, plus `build/test-results.xml`.

Open waveforms with GTKWave or Surfer. Both ship with OSS CAD Suite and open through WSLg, for example
`.tools/oss-cad-suite/bin/surfer build/sim/.../dump.fst`. Any Windows viewer works too.

### Adding a module

1. Add `firmware/rtl/<area>/<name>.sv` (file name = module name) and list it in a filelist under `firmware/filelists/`.
2. Add `firmware/tb/unit/test_<name>.py` with `tc_*` cocotb coroutines and a `test_<name>()` pytest entry point
   that calls `common.runner.run_cocotb`.
3. Run `just fmt lint test`.

See [`firmware/CODING_STYLE.md`](../firmware/CODING_STYLE.md) for the rules.

## Editor integration

Lint rules and formatting flags are stored in the repo, so editors and `just lint` agree:

- **Verible lint:** `.rules.verible_lint` at the repo root. `verible-verilog-ls` finds it when it runs with
  `--ruleset=all --rules_config_search`.
- **Verible format:** `firmware/lint/verible-format.flags`. Point the editor's formatter at it with
  `--flagfile=firmware/lint/verible-format.flags`, or use the same flags.
- **Verilator:** there is no language server. `just lint` is the reference.

## Vivado (optional, Windows)

Vivado is not needed for lint or tests. It is used for synthesis sanity checks and timing/utilization reports, and to
run the legacy XSIM testbench.

```powershell
just vivado-project                      # build/vivado/xc7a100tcsg324-1/hft.xpr (no board part)
just synth-ooc reset_sync                # build/vivado/ooc/reset_sync/{timing_summary,utilization}.rpt
just synth-ooc HFT_Top_Core xc7a100tcsg324-1 "legacy.f common.f"
```

The project file is generated and never committed. Sources come from the same filelists that Verilator uses.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `fatal: detected dubious ownership` in WSL | `git config --global --add safe.directory /mnt/d/<path-to-repo>` |
| `just: command not found` in WSL | Use a login shell (`bash -l`) or add `~/.local/bin` to `PATH` |
| `Permission denied` running a recipe script | `XDG_RUNTIME_DIR` is `noexec`; script recipes already use `[script("bash")]`. Keep it that way for new ones |
| Slow first build on `/mnt/d` | Exclude the repo's `build/` and `.tools/` folders from Windows Defender real-time scanning, or set `HFT_BUILD_DIR` to a WSL path |
| Lint does not flag a signal named `*unused*` | Verilator ignores names matching `--unused-regexp` (`*unused*`) on purpose |
| `Unsupported arch` | 32-bit Pi OS is not supported. Install the 64-bit image |
| Wrong `python` inside recipes | Do not source OSS CAD Suite's `environment` script; the justfile only adds its `bin/` to `PATH` |
| Verilator too old or broken | `scripts/build-verilator.sh [tag]` builds a pinned Verilator into `.tools/verilator` |
| XSIM `compile.bat is not recognized` | The environment variable `NoDefaultCurrentDirectoryInExePath` is set; unset it for Vivado |
