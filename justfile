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
# Reference part of the F5 timing gate (audit 10 §2.13): Kintex UltraScale+, the class of a real
# 10G + PCIe Gen3 x8 card. Needs the Kintex UltraScale+ device files in the Vivado install.
ref_part := "xcku5p-ffvb676-2-e"

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

# SystemVerilog files under the lint gate, one per line: KIND PATH. KIND is
# rtl (Verilator top = file name, -F common.f), core (+ core.f), csr (+ core.f and csr.f),
# gen (as csr, but generated, in core.f or csr.f: Verilator only, no Verible style/format), top (+ core.f, csr.f
# and top.f),
# ooc (+ synth_ooc.f), ooc_core (+ core.f and synth_ooc_core.f), ooc_csr (+ core.f, csr.f and
# synth_ooc_csr.f), ooc_top (+ core.f, csr.f, top.f and synth_ooc_top.f) or style (Verible only: XPM wrappers that Verilator cannot compile, SVA
# checkers and self-checking SV testbenches).
[private]
[script("bash")]
[unix]
sv-files:
    set -euo pipefail
    # `|| true`: a filelist may hold only comments until its first file arrives.
    # A missing filelist is an error, not an empty list (its files would silently skip lint).
    files_of() {
      [ -f "firmware/filelists/$1" ] || { echo "sv-files: missing filelist $1" >&2; exit 1; }
      { grep -vE '^\s*(#|//)' "firmware/filelists/$1" || true; } | sed '/^\s*$/d; s|^|firmware/filelists/|'
    }
    files_of common.f | sed 's/^/rtl /'
    files_of core.f | sed -E 's|^(.*/gen/.*)$|gen \1|; t; s/^/core /'
    files_of csr.f | sed -E 's|^(.*/gen/.*)$|gen \1|; t; s/^/csr /'
    files_of top.f | sed 's/^/top /'
    files_of synth_ooc.f | sed 's/^/ooc /'
    files_of synth_ooc_core.f | sed 's/^/ooc_core /'
    files_of synth_ooc_csr.f | sed 's/^/ooc_csr /'
    files_of synth_ooc_top.f | sed 's/^/ooc_top /'
    { files_of xilinx.f; files_of sva.f; files_of sva_core.f; files_of sva_csr.f; ls firmware/tb/sv/*.sv; } | sed 's/^/style /'

# Lint gate (blocking): Verilator -Wall per module, Verible rules (.rules.verible_lint) and format check
[script("bash")]
[unix]
lint:
    set -uo pipefail
    fail=0
    srcs=()
    list=$(just sv-files) || { echo "lint: just sv-files failed"; exit 1; }
    while read -r kind f; do
      [ "$kind" = gen ] || srcs+=("$f")   # generated files: Verilator only (never restyled by hand)
      [ "$kind" = style ] && continue
      case "$f" in *_pkg.sv) continue ;; esac   # packages are linted through their users (no module top)
      top=$(basename "$f" .sv)
      lists=(-F firmware/filelists/common.f)
      case "$kind" in
        core)     lists+=(-F firmware/filelists/core.f) ;;
        csr|gen)  lists+=(-F firmware/filelists/core.f -F firmware/filelists/csr.f) ;;
        top)      lists+=(-F firmware/filelists/core.f -F firmware/filelists/csr.f -F firmware/filelists/top.f) ;;
        ooc)      lists+=(-F firmware/filelists/synth_ooc.f) ;;
        ooc_core) lists+=(-F firmware/filelists/core.f -F firmware/filelists/synth_ooc_core.f) ;;
        ooc_csr)  lists+=(-F firmware/filelists/core.f -F firmware/filelists/csr.f -F firmware/filelists/synth_ooc_csr.f) ;;
        ooc_top)  lists+=(-F firmware/filelists/core.f -F firmware/filelists/csr.f -F firmware/filelists/top.f
                    -F firmware/filelists/synth_ooc_top.f) ;;
      esac
      echo "== verilator $top"
      verilator --lint-only -Wall --quiet --top-module "$top" firmware/lint/waivers.vlt "${lists[@]}" || fail=1
    done <<<"$list"
    [ ${#srcs[@]} -gt 0 ] || { echo "lint: no files found"; exit 1; }
    echo "== verible-verilog-lint"
    verible-verilog-lint --ruleset=all --rules_config=.rules.verible_lint "${srcs[@]}" || fail=1
    echo "== verible-verilog-format --verify"
    for f in "${srcs[@]}"; do
      verible-verilog-format --flagfile=firmware/lint/verible-format.flags --verify "$f" || fail=1
    done
    echo "lint: ${#srcs[@]} file(s), $([ $fail = 0 ] && echo clean || echo FAILED)"
    exit $fail

# Run all cocotb tests in parallel (extra pytest args are passed through as given, e.g. -k "a or b")
[positional-arguments]
[unix]
test *ARGS:
    uv run pytest -n auto --dist loadgroup "$@"

# Run the long tests only (marker long): minutes each, part of `just ci`
[positional-arguments]
[unix]
test-long *ARGS:
    uv run pytest -n auto --dist loadgroup -m long --junitxml=build/long-results.xml "$@"

# Run tests matching NAME (pytest -k)
[unix]
test-one NAME *ARGS:
    uv run pytest -k '{{ NAME }}' {{ ARGS }}

# Real-data model check (audit/12 S11): download the manifest's Nasdaq ranges into build/itch-real
# (firmware/tb/data/itch_real.toml, SHA-256 checked; the data never enters the repo) and check the
# ITCH and Mold models on them (a few minutes)
[positional-arguments]
[unix]
itch-real *ARGS:
    uv run pytest -n auto --dist loadgroup -m real --junitxml=build/itch-real-results.xml firmware/tb/real "$@"

# Real data through the RTL: itch_decoder on every message of each range (about 75 min per source)
# and the system test on its windows
[positional-arguments]
[unix]
itch-real-rtl *ARGS:
    uv run pytest -n auto --dist loadgroup -m real --junitxml=build/itch-real-rtl-results.xml firmware/tb/unit firmware/tb/system "$@"

# Re-run one test with FST tracing; prints the wave file paths.
[unix]
waves NAME:
    WAVES=1 uv run pytest -k '{{ NAME }}' -p no:xdist
    @find "$HFT_BUILD_DIR/sim" -name '*.fst' -newer pyproject.toml -print

# Format every file under the style gate in place (same flags as the editor: firmware/lint/verible-format.flags).
# Generated files (kind gen) are left alone: only `just gen` writes them (just gen-check).
[script("bash")]
[unix]
fmt:
    set -euo pipefail
    just sv-files | while read -r kind f; do
      [ "$kind" = gen ] && continue
      verible-verilog-format --flagfile=firmware/lint/verible-format.flags --inplace "$f"
    done

# Regenerate the CSR block, C header and map doc from firmware/rdl/hft_csr.rdl (PeakRDL) and the
# event descriptor outputs from firmware/formats/event_desc.toml
[unix]
gen:
    bash scripts/gen_csr.sh
    uv run --frozen python scripts/gen_desc.py

# Fail if the committed generated files differ from a fresh `just gen` (edited RDL or schema
# without gen, or a hand-edited generated file)
[script("bash")]
[unix]
gen-check:
    set -euo pipefail
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    bash scripts/gen_csr.sh "$tmp"
    uv run --frozen python scripts/gen_desc.py "$tmp"
    desc=$(uv run --frozen python scripts/gen_desc.py --list)
    fail=0
    for d in firmware/rtl/core/gen software/include/gen firmware/rdl/hft_csr.md $desc; do
      diff -r "$tmp/$d" "$d" || fail=1
    done
    [ $fail = 0 ] && echo "gen-check: generated files are up to date" || { echo "gen-check: FAILED (run: just gen)"; exit 1; }

# doctor + gen-check + lint + test + test-long
[unix]
ci: doctor gen-check lint test test-long

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

# Run all cocotb tests in WSL
[windows]
test *ARGS:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just test {{ ARGS }}"

# Run the long tests only in WSL
[windows]
test-long *ARGS:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just test-long {{ ARGS }}"

# Run tests matching NAME in WSL
[windows]
test-one NAME *ARGS:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just test-one '{{ NAME }}' {{ ARGS }}"

# Real-data model check in WSL
[windows]
itch-real *ARGS:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just itch-real {{ ARGS }}"

# Real data through the RTL in WSL
[windows]
itch-real-rtl *ARGS:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just itch-real-rtl {{ ARGS }}"

# Re-run one test with FST tracing in WSL
[windows]
waves NAME:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just waves '{{ NAME }}'"

# Format new RTL in WSL
[windows]
fmt:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just fmt"

# Regenerate the CSR and event descriptor outputs in WSL
[windows]
gen:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just gen"

# Check the committed generated files in WSL
[windows]
gen-check:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just gen-check"

# doctor + gen-check + lint + test + test-long in WSL
[windows]
ci:
    wsl -d {{ distro }} --cd "{{ root }}" -- bash -lc "just ci"

# ---------- Vivado (Windows, or Linux with vivado on PATH) ----------

# Regenerate the board-less Vivado project under build/vivado/<PART>
[windows]
vivado-project PART=fpga_part:
    New-Item -ItemType Directory -Force build/vivado | Out-Null; Set-Location build/vivado; & "{{ vivado }}" -mode batch -nojournal -log create_project.log -source ../../firmware/scripts/vivado/create_project.tcl -tclargs {{ PART }}

# OOC synthesis + CDC constraints + timing/report_cdc gate. ARGS: extra filelists or NAME:VALUE generics
[windows]
synth-ooc TOP PROFILE="net2host" *ARGS:
    just synth-ooc-on {{ fpga_part }} {{ TOP }} {{ PROFILE }} {{ ARGS }}

# synth-ooc on a given part (e.g. the reference part: just synth-ooc-on xcku5p-ffvb676-2-e ...)
[windows]
synth-ooc-on PART TOP PROFILE="net2host" *ARGS:
    $t = -join (@('{{ ARGS }}'.Split(' ')) | Where-Object { $_ -match ':' -or $_ -eq 'impl' } | ForEach-Object { '_' + ($_ -replace ':', '') }); New-Item -ItemType Directory -Force build/vivado | Out-Null; Set-Location build/vivado; & "{{ vivado }}" -mode batch -nojournal -log "synth_{{ TOP }}_{{ PROFILE }}_{{ PART }}$t.log" -source ../../firmware/scripts/vivado/synth_ooc.tcl -tclargs {{ TOP }} {{ PART }} {{ PROFILE }} common.f xilinx.f synth_ooc.f {{ ARGS }}

# F5 gate: hft_csr_wrap placed and routed on ref_part (156.25/250 MHz) and the A7 (156.25/125 MHz)
[windows]
[script("powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File")]
[extension(".ps1")]
synth-csr:
    $csr = @("core.f", "csr.f", "synth_ooc_csr.f")
    $runs = @(
      @("gate", "{{ ref_part }}", "hft_csr_wrap_ooc", "net2host", ($csr + "impl")),
      @("gate", "{{ fpga_part }}", "hft_csr_wrap_ooc", "net2host125", ($csr + "impl")),
      @("info", "{{ fpga_part }}", "hft_csr_wrap_ooc", "net2host", $csr),
      @("info", "{{ ref_part }}", "eth_ip_udp_rx_ooc", "net2host", @("core.f", "synth_ooc_core.f"))
    )
    $bad = 0
    foreach ($r in $runs) {
      $out = just synth-ooc-on $r[1] $r[2] $r[3] @($r[4]) 2>&1
      $rc = $LASTEXITCODE
      ($out | Select-String "^HFT_SYNTH").Line | ForEach-Object { "$($r[0]): $_" }
      if ($rc -ne 0 -and $r[0] -eq "gate") { $bad++ }
    }
    if ($bad) { Write-Host "synth-csr: $bad gate run(s) FAILED"; exit 1 }
    Write-Host "synth-csr: gates PASS"

# 250 MHz, information only: just synth-ooc eth_ip_udp_rx_ooc host2net core.f synth_ooc_core.f
# The event path (packer, FIFO, ring writer) runs with its host clock: ref_part 250 MHz, the A7 125 MHz.
# Core blocks at net_clk (156.25 MHz) and the event path, placed and routed on the A7 and ref_part, gated
[windows]
[script("powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File")]
[extension(".ps1")]
synth-core:
    $runs = @()
    foreach ($top in "eth_ip_udp_rx_ooc", "moldudp64_rx_ooc", "itch_decoder_ooc", "order_path_ooc") {
      foreach ($part in "{{ fpga_part }}", "{{ ref_part }}") { $runs += ,@($part, $top, "net2host") }
    }
    $runs += ,@("{{ ref_part }}", "event_path_ooc", "net2host")
    $runs += ,@("{{ fpga_part }}", "event_path_ooc", "net2host125")
    $bad = 0
    foreach ($r in $runs) {
      $out = just synth-ooc-on $r[0] $r[1] $r[2] core.f synth_ooc_core.f impl 2>&1
      if ($LASTEXITCODE) { $bad++ }
      ($out | Select-String "^HFT_SYNTH").Line
    }
    if ($bad) { Write-Host "synth-core: $bad gate run(s) FAILED"; exit 1 }
    Write-Host "synth-core: gates PASS"

# RING_DATA_W = 512 is gated on ref_part (156.25/250 MHz) and the A7 (156.25/125 MHz); RING_DATA_W = 128 on the A7
# (the PCIe Gen2 x4 bridge width) is information only.
# F8 gate: the whole core (HFT_Core) placed and routed
[windows]
[script("powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File")]
[extension(".ps1")]
synth-top:
    $top = @("core.f", "csr.f", "top.f", "synth_ooc_top.f")
    $runs = @(
      @("gate", "{{ ref_part }}", "net2host", ($top + "impl")),
      @("gate", "{{ fpga_part }}", "net2host125", ($top + "impl")),
      @("info", "{{ fpga_part }}", "net2host125", ($top + "RING_DATA_W:128" + "impl"))
    )
    $bad = 0
    $sum = @()
    foreach ($r in $runs) {
      # Vivado's output streams live (a run takes up to an hour); the summary lines are repeated at the end.
      $out = [System.Collections.Generic.List[string]]::new()
      just synth-ooc-on $r[1] hft_core_ooc $r[2] @($r[3]) 2>&1 | ForEach-Object { $out.Add("$_"); "$_" }
      $rc = $LASTEXITCODE
      $sum += ($out | Select-String "^HFT_(SYNTH|SLACK|STAGE)").Line | ForEach-Object { "$($r[0]): $_" }
      if ($rc -ne 0) { $sum += "$($r[0]): exit code $rc ($($r[1]) $($r[2]) $($r[3] -join ' '))" }
      if ($rc -ne 0 -and $r[0] -eq "gate") { $bad++ }
    }
    "==== synth-top summary ===="
    $sum
    if ($bad) { Write-Host "synth-top: $bad gate run(s) FAILED"; exit 1 }
    Write-Host "synth-top: gates PASS"

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
    just synth-ooc-on {{ fpga_part }} {{ TOP }} {{ PROFILE }} {{ ARGS }}

# synth-ooc on a given part (e.g. the reference part: just synth-ooc-on xcku5p-ffvb676-2-e ...)
[unix]
synth-ooc-on PART TOP PROFILE="net2host" *ARGS:
    t=$(for a in {{ ARGS }}; do case "$a" in *:*|impl) printf '_%s' "$(printf %s "$a" | tr -d :)";; esac; done); mkdir -p build/vivado && cd build/vivado && {{ vivado }} -mode batch -nojournal -log "synth_{{ TOP }}_{{ PROFILE }}_{{ PART }}$t.log" -source ../../firmware/scripts/vivado/synth_ooc.tcl -tclargs {{ TOP }} {{ PART }} {{ PROFILE }} common.f xilinx.f synth_ooc.f {{ ARGS }}

# F5 gate: hft_csr_wrap placed and routed on ref_part (156.25/250 MHz) and the A7 (156.25/125 MHz)
[script("bash")]
[unix]
synth-csr:
    bad=0
    run() {  # gate|info PART TOP PROFILE LISTS...
      local kind=$1; shift
      out=$(just synth-ooc-on "$@" 2>&1); rc=$?
      grep '^HFT_SYNTH' <<<"$out" | sed "s/^/$kind: /"
      [ $rc = 0 ] || [ "$kind" = info ] || bad=$((bad + 1))
    }
    run gate {{ ref_part }} hft_csr_wrap_ooc net2host core.f csr.f synth_ooc_csr.f impl
    run gate {{ fpga_part }} hft_csr_wrap_ooc net2host125 core.f csr.f synth_ooc_csr.f impl
    run info {{ fpga_part }} hft_csr_wrap_ooc net2host core.f csr.f synth_ooc_csr.f
    run info {{ ref_part }} eth_ip_udp_rx_ooc net2host core.f synth_ooc_core.f
    [ $bad = 0 ] && echo "synth-csr: gates PASS" || { echo "synth-csr: $bad gate run(s) FAILED"; exit 1; }

# 250 MHz, information only: just synth-ooc eth_ip_udp_rx_ooc host2net core.f synth_ooc_core.f
# The event path (packer, FIFO, ring writer) runs with its host clock: ref_part 250 MHz, the A7 125 MHz.
# Core blocks at net_clk (156.25 MHz) and the event path, placed and routed on the A7 and ref_part, gated
[script("bash")]
[unix]
synth-core:
    bad=0
    run() {  # PART TOP PROFILE
      out=$(just synth-ooc-on "$1" "$2" "$3" core.f synth_ooc_core.f impl 2>&1); rc=$?
      grep '^HFT_SYNTH' <<<"$out"
      [ $rc = 0 ] || bad=$((bad + 1))
    }
    for top in eth_ip_udp_rx_ooc moldudp64_rx_ooc itch_decoder_ooc order_path_ooc; do
      for part in {{ fpga_part }} {{ ref_part }}; do
        run "$part" "$top" net2host
      done
    done
    run {{ ref_part }} event_path_ooc net2host
    run {{ fpga_part }} event_path_ooc net2host125
    [ $bad = 0 ] && echo "synth-core: gates PASS" || { echo "synth-core: $bad gate run(s) FAILED"; exit 1; }

# RING_DATA_W = 512 is gated on ref_part (156.25/250 MHz) and the A7 (156.25/125 MHz); RING_DATA_W = 128 on the A7
# (the PCIe Gen2 x4 bridge width) is information only.
# F8 gate: the whole core (HFT_Core) placed and routed
[script("bash")]
[unix]
synth-top:
    bad=0
    sum=""
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' EXIT
    run() {  # gate|info PART PROFILE ARGS...
      local kind=$1; shift
      # Vivado's output streams live (a run takes up to an hour); the summary lines are repeated at the end.
      just synth-ooc-on "$1" hft_core_ooc "${@:2}" core.f csr.f top.f synth_ooc_top.f impl 2>&1 | tee "$tmp"
      rc=${PIPESTATUS[0]}
      sum+="$(grep -E '^HFT_(SYNTH|SLACK|STAGE)' "$tmp" | sed "s/^/$kind: /")"$'\n'
      [ $rc = 0 ] || sum+="$kind: exit code $rc ($*)"$'\n'
      [ $rc = 0 ] || [ "$kind" = info ] || bad=$((bad + 1))
    }
    run gate {{ ref_part }} net2host
    run gate {{ fpga_part }} net2host125
    run info {{ fpga_part }} net2host125 RING_DATA_W:128
    echo "==== synth-top summary ===="
    printf '%s' "$sum"
    [ $bad = 0 ] && echo "synth-top: gates PASS" || { echo "synth-top: $bad gate run(s) FAILED"; exit 1; }

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
