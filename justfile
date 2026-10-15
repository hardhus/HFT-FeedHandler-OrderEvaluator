# HFT FeedHandler: single entry point. Run `just` to list recipes.
# Linux / WSL / Raspberry Pi 5 run recipes natively; on Windows the
# simulation recipes are forwarded to WSL (see HFT_WSL_DISTRO).

set dotenv-load := true
set dotenv-filename := ".env.local"
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

root := justfile_directory()
distro := env("HFT_WSL_DISTRO", "Debian")
vivado := env("VIVADO", "vivado")
build := if env("HFT_BUILD_DIR", "") == "" { root / "build" } else { env("HFT_BUILD_DIR") }
fpga_part := "xc7a100tcsg324-1"

# Linux: prepend the pinned toolchain. Windows: PATH untouched.

export PATH := if os_family() == "windows" { env("PATH") } else { root / ".tools/verible/bin" + ":" + root / ".tools/oss-cad-suite/bin" + ":" + env("PATH") }
export HFT_BUILD_DIR := build

default:
    @just --list

# ---------- Linux (WSL / Pi) ----------

# Install apt prerequisites, pinned OSS CAD Suite + Verible and the Python env
[unix]
setup:
    bash scripts/setup-linux.sh

# Check that every required tool is present and recent enough
[unix]
doctor:
    bash scripts/doctor.sh

# SystemVerilog files under the style gate, one per line: KIND PATH. KIND is
# rtl (Verilator top = file name, -F common.f), core (+ core.f), ooc (+ synth_ooc.f),
# ooc_core (+ core.f and synth_ooc_core.f) or style (Verible only: XPM wrappers that Verilator
# cannot compile, SVA checkers and self-checking SV testbenches).
[private]
[script("bash")]
[unix]
sv-files:
    set -euo pipefail
    # `|| true`: a filelist may hold only comments until its first file arrives.
    files_of() { { grep -vE '^\s*(#|//)' "firmware/filelists/$1" || true; } | sed '/^\s*$/d; s|^|firmware/filelists/|'; }
    files_of common.f | sed 's/^/rtl /'
    files_of core.f | sed 's/^/core /'
    files_of synth_ooc.f | sed 's/^/ooc /'
    files_of synth_ooc_core.f | sed 's/^/ooc_core /'
    { files_of xilinx.f; files_of sva.f; files_of sva_core.f; ls firmware/tb/sv/*.sv; } | sed 's/^/style /'

# Lint gate (blocking): Verilator -Wall per module, Verible rules (.rules.verible_lint) and format check
[script("bash")]
[unix]
lint:
    set -uo pipefail
    fail=0
    srcs=()
    while read -r kind f; do
      srcs+=("$f")
      [ "$kind" = style ] && continue
      case "$f" in *_pkg.sv) continue ;; esac   # packages are linted through their users (no module top)
      top=$(basename "$f" .sv)
      lists=(-F firmware/filelists/common.f)
      case "$kind" in
        core)     lists+=(-F firmware/filelists/core.f) ;;
        ooc)      lists+=(-F firmware/filelists/synth_ooc.f) ;;
        ooc_core) lists+=(-F firmware/filelists/core.f -F firmware/filelists/synth_ooc_core.f) ;;
      esac
      echo "== verilator $top"
      verilator --lint-only -Wall --quiet --top-module "$top" firmware/lint/waivers.vlt "${lists[@]}" || fail=1
    done < <(just sv-files)
    [ ${#srcs[@]} -gt 0 ] || { echo "lint: no files found"; exit 1; }
    echo "== verible-verilog-lint"
    verible-verilog-lint --ruleset=all --rules_config=.rules.verible_lint "${srcs[@]}" || fail=1
    echo "== verible-verilog-format --verify"
    for f in "${srcs[@]}"; do
      verible-verilog-format --flagfile=firmware/lint/verible-format.flags --verify "$f" || fail=1
    done
    echo "lint: ${#srcs[@]} file(s), $([ $fail = 0 ] && echo clean || echo FAILED)"
    exit $fail

# Lint legacy RTL for the record (non-blocking). XPM-dependent modules are skipped.
[script("bash")]
[unix]
lint-legacy:
    mkdir -p "$HFT_BUILD_DIR/lint"
    log="$HFT_BUILD_DIR/lint/legacy.log"
    : > "$log"
    L=firmware/rtl/legacy
    verilator --lint-only -Wall --top-module udp_parser    $L/hft_pkg.sv $L/axis_if.sv $L/udp_parser.sv    >>"$log" 2>&1
    verilator --lint-only -Wall --top-module register_bank $L/hft_pkg.sv $L/axil_if.sv $L/register_bank.sv >>"$log" 2>&1
    echo "skipped: HFT_Top, async_fifo_wrapper (need xpm_fifo_async)" >> "$log"
    echo "== verible (style, all legacy files)" >> "$log"
    verible-verilog-lint --ruleset=all --rules_config=.rules.verible_lint $L/*.sv >>"$log" 2>&1
    echo "legacy lint: $(grep -cE '%(Warning|Error)' "$log") verilator + $(grep -cE '\[Style: ' "$log") verible findings -> $log"

# Run all cocotb tests in parallel (extra pytest args are passed through as given, e.g. -k "a or b")
[positional-arguments]
[unix]
test *ARGS:
    uv run pytest -n auto --dist loadgroup "$@"

# Run tests matching NAME (pytest -k)
[unix]
test-one NAME *ARGS:
    uv run pytest -k '{{ NAME }}' {{ ARGS }}

# Re-run one test with FST tracing; prints the wave file paths.
[unix]
waves NAME:
    WAVES=1 uv run pytest -k '{{ NAME }}' -p no:xdist
    @find "$HFT_BUILD_DIR/sim" -name '*.fst' -newer pyproject.toml -print

# Format every file under the style gate in place (same flags as the editor: firmware/lint/verible-format.flags)
[script("bash")]
[unix]
fmt:
    set -euo pipefail
    just sv-files | while read -r _ f; do
      verible-verilog-format --flagfile=firmware/lint/verible-format.flags --inplace "$f"
    done

# doctor + lint + test
[unix]
ci: doctor lint test

# ---------- Windows: forward Linux recipes to WSL ----------

# Bootstrap WSL (uv, just, pinned tools, Python env)
[windows]
setup:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "./scripts/bootstrap.sh"

# Check the WSL toolchain
[windows]
doctor:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just doctor"

# Lint new RTL in WSL (Verilator + Verible + format check)
[windows]
lint:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just lint"

# Lint legacy RTL in WSL (report only)
[windows]
lint-legacy:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just lint-legacy"

# Run all cocotb tests in WSL
[windows]
test *ARGS:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just test {{ ARGS }}"

# Run tests matching NAME in WSL
[windows]
test-one NAME *ARGS:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just test-one '{{ NAME }}' {{ ARGS }}"

# Re-run one test with FST tracing in WSL
[windows]
waves NAME:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just waves '{{ NAME }}'"

# Format new RTL in WSL
[windows]
fmt:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just fmt"

# doctor + lint + test in WSL
[windows]
ci:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just ci"

# ---------- Everywhere ----------

# CSR generation arrives in F5 (PeakRDL).
gen:
    @echo "gen: nothing to generate yet (F5)"

# ---------- Vivado (Windows, or Linux with vivado on PATH) ----------

# Regenerate the board-less Vivado project under build/vivado/<PART>
[windows]
vivado-project PART=fpga_part:
    New-Item -ItemType Directory -Force build/vivado | Out-Null; Set-Location build/vivado; & "{{ vivado }}" -mode batch -nojournal -log create_project.log -source ../../firmware/scripts/vivado/create_project.tcl -tclargs {{ PART }}

# OOC synthesis + CDC constraints + timing/report_cdc gate. ARGS: extra filelists or NAME:VALUE generics
[windows]
synth-ooc TOP PROFILE="net2host" *ARGS:
    New-Item -ItemType Directory -Force build/vivado | Out-Null; Set-Location build/vivado; & "{{ vivado }}" -mode batch -nojournal -log synth_{{ TOP }}_{{ PROFILE }}.log -source ../../firmware/scripts/vivado/synth_ooc.tcl -tclargs {{ TOP }} {{ fpga_part }} {{ PROFILE }} common.f xilinx.f synth_ooc.f {{ ARGS }}

# Gate every common block in both clock directions; prints one HFT_SYNTH line per run
[windows]
synth-common:
    $runs = @("reset_sync","sync_bit","axis_register","axis_skid_buffer","cdc_handshake","cdc_async_fifo_ooc","cdc_async_fifo_ooc IMPL:xpm"); $bad = 0; foreach ($r in $runs) { foreach ($p in "net2host","host2net") { $a = $r.Split(" "); $out = just synth-ooc $a[0] $p @($a | Select-Object -Skip 1) 2>&1; if ($LASTEXITCODE) { $bad++ }; ($out | Select-String "^HFT_SYNTH").Line } }; if ($bad) { Write-Host "synth-common: $bad run(s) FAILED"; exit 1 }; Write-Host "synth-common: all PASS"

# Cross-simulator FIFO check in XSIM: IMPL = portable | xpm (same TB and seed as `just test`)
[windows]
xsim-test IMPL="xpm" SEED="1":
    New-Item -ItemType Directory -Force build/vivado | Out-Null; Set-Location build/vivado; & "{{ vivado }}" -mode batch -nojournal -log xsim_{{ IMPL }}.log -source ../../firmware/scripts/vivado/xsim_run.tcl -tclargs {{ IMPL }} {{ SEED }}

# Cross-simulator FIFO check in XSIM: IMPL = portable | xpm (same TB and seed as `just test`)
[unix]
xsim-test IMPL="xpm" SEED="1":
    mkdir -p build/vivado && cd build/vivado && {{ vivado }} -mode batch -nojournal -log xsim_{{ IMPL }}.log -source ../../firmware/scripts/vivado/xsim_run.tcl -tclargs {{ IMPL }} {{ SEED }}

# Regenerate the board-less Vivado project under build/vivado/<PART>
[unix]
vivado-project PART=fpga_part:
    mkdir -p build/vivado && cd build/vivado && {{ vivado }} -mode batch -nojournal -log create_project.log -source ../../firmware/scripts/vivado/create_project.tcl -tclargs {{ PART }}

# OOC synthesis + CDC constraints + timing/report_cdc gate. ARGS: extra filelists or NAME:VALUE generics
[unix]
synth-ooc TOP PROFILE="net2host" *ARGS:
    mkdir -p build/vivado && cd build/vivado && {{ vivado }} -mode batch -nojournal -log synth_{{ TOP }}_{{ PROFILE }}.log -source ../../firmware/scripts/vivado/synth_ooc.tcl -tclargs {{ TOP }} {{ fpga_part }} {{ PROFILE }} common.f xilinx.f synth_ooc.f {{ ARGS }}

# Gate every common block in both clock directions; prints one HFT_SYNTH line per run
[script("bash")]
[unix]
synth-common:
    bad=0
    for r in reset_sync sync_bit axis_register axis_skid_buffer cdc_handshake cdc_async_fifo_ooc "cdc_async_fifo_ooc IMPL:xpm"; do
      for p in net2host host2net; do
        set -- $r
        out=$(just synth-ooc "$1" "$p" "${@:2}" 2>&1)
        rc=$?
        grep '^HFT_SYNTH' <<<"$out"
        [ $rc = 0 ] || bad=$((bad + 1))
      done
    done
    [ $bad = 0 ] && echo "synth-common: all PASS" || { echo "synth-common: $bad run(s) FAILED"; exit 1; }

# Remove build outputs (keeps .tools/ and .venv/)
clean:
    @echo "removing {{ build }}"
    {{ if os_family() == "windows" { "Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '" + build + "'" } else { "rm -rf '" + build + "'" } }}
