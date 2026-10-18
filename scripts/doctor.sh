#!/usr/bin/env bash
# Environment check: PASS/FAIL/INFO per line, exit 1 if a required tool is missing.
set -uo pipefail
cd "$(dirname "$0")/.."
source tools.env
fail=0
ok()   { printf '\e[32mPASS\e[0m %s\n' "$*"; }
bad()  { printf '\e[31mFAIL\e[0m %s\n' "$*"; fail=1; }
info() { printf '\e[34mINFO\e[0m %s\n' "$*"; }
ver_ge() { printf '%s\n%s\n' "$2" "$1" | sort -V -C; }   # ver_ge <have> <min>

info "arch: $(uname -m), os: $(. /etc/os-release; echo "$PRETTY_NAME")"
for t in git make g++ perl uv just; do
  if command -v "$t" >/dev/null; then ok "$t"; else bad "$t missing (run: ./scripts/bootstrap.sh)"; fi
done

for h in zlib.h lz4.h; do
  if [[ -f /usr/include/$h ]]; then ok "$h (FST tracing)"; else bad "$h missing (run: just setup)"; fi
done

if command -v verilator >/dev/null; then
  v=$(verilator --version | awk '{print $2}')
  if ver_ge "$v" "$VERILATOR_MIN"; then ok "verilator $v (>= $VERILATOR_MIN)"; else bad "verilator $v < $VERILATOR_MIN"; fi
  case "$(command -v verilator)" in
    "$PWD"/.tools/*) ok "verilator from pinned OSS CAD Suite ($(cat .tools/oss-cad-suite/.hft-version 2>/dev/null))" ;;
    *) info "verilator not from .tools/ ($(command -v verilator))" ;;
  esac
else
  bad "verilator missing (run: just setup)"
fi

if [[ -f pyproject.toml ]]; then
  if cv=$(uv run --frozen python -c 'import cocotb; print(cocotb.__version__)' 2>/dev/null); then
    ok "cocotb $cv"
  else
    bad "cocotb not importable (run: just setup)"
  fi
  if rv=$(uv run --frozen python -c 'from importlib.metadata import version as v; print(v("peakrdl-regblock"))' 2>/dev/null); then
    ok "peakrdl-regblock $rv (just gen)"
  else
    bad "PeakRDL not importable (run: just setup)"
  fi
else
  info "pyproject.toml not present yet; skipping Python checks"
fi

if command -v gtkwave >/dev/null; then info "gtkwave available"; fi
if command -v surfer  >/dev/null; then info "surfer available"; fi
if command -v verible-verilog-lint >/dev/null && command -v verible-verilog-format >/dev/null; then
  vv=$(verible-verilog-lint --version | awk '/^Version/ {print $2}')
  if [[ "$vv" == "$VERIBLE_VERSION" ]]; then ok "verible $vv"; else bad "verible $vv != pinned $VERIBLE_VERSION (run: just setup)"; fi
else
  bad "verible missing (run: just setup)"
fi
exit $fail
