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
just fmt                  # format every file under the lint gate in place (not generated files)
just gen                  # regenerate the CSR outputs (hft_csr.rdl) and the event descriptor outputs (event_desc.toml)
just gen-check            # fail if a generated file differs from a fresh `just gen` (part of `just ci`)
just test                 # all cocotb tests, in parallel
just test-one reset_sync  # tests matching a name (pytest -k)
just test-one net_model   # golden-model self-tests only (no simulator, seconds)
just waves reset_sync     # re-run with FST tracing and print the .fst paths
just lint-legacy          # lint report for the legacy RTL -> build/lint/legacy.log
just itch-real            # real Nasdaq data: the model check (downloads into build/itch-real, a few minutes)
just itch-real-rtl        # real Nasdaq data through itch_decoder and the system test (see below)
```

`just lint` covers:
- **Verilator `-Wall` + Verible:** every module in `common.f`, `core.f`, `csr.f` and `system.f` (each as its own
  top, with the lists it needs) and the OOC synthesis harnesses in `synth_ooc*.f`. Packages (`*_pkg.sv`) are not a
  top of their own: Verilator checks them through their users. One top need not use every `hft_pkg` constant, so
  that warning is waived for `hft_pkg`; the selftest `test_every_package_constant_has_a_user` checks that every
  constant has a user somewhere;
- **Verilator only:** the generated files under `rtl/core/gen/` (they are never restyled by hand; their waivers in
  `firmware/lint/waivers.vlt` match file, rule and message);
- **Verible only (lint + format):** the XPM wrappers in `xilinx.f` (Verilator cannot compile XPM), the SVA checkers
  in `sva*.f` and the self-checking SV testbenches in `firmware/tb/sv/`.

The list comes from the filelists (`just sv-files` prints it), so a new file is linted as soon as it is listed.

Build outputs go to `build/`:
- `build/sim/<top>_<hash>/` holds one cocotb build per configuration, with one `run_<hash>/` per runtime environment;
- `build/svtb/` holds SV-testbench builds;
- `build/test-results.xml` is the JUnit report.

### Builds, seeds and reproducing a failure

- **Builds are reused.** The build directory name hashes the toplevel, parameters, defines, flags, every source file
  and `tools.env`. Changing any of them gives a new directory; an unchanged configuration is not recompiled. Old
  directories pile up; `just clean` removes them.
  The hash covers every file of the filelists a build uses, so editing any file in `common.f` (or `sva.f`) rebuilds
  every test that uses that list. Expect a cold `just test` after such a change. Core assertions therefore live in
  their own list: a build with `core.f` also compiles `sva_core.f` (`SVA_FOR` in `runner.py`), and editing them
  rebuilds only the core tests.
  For reference (WSL, 2 workers, 173 tests after F2): a warm `just test` takes about 2.3 minutes. After F5
  (235 tests) a full `just ci` takes about 11.5 minutes on WSL and 12 minutes on the Raspberry Pi 5; the longest
  entries are the two `tc_rx_csr_random` runs (2000 frames with commits and snapshots, about 75 s each). The longest test
  is `tc_random_golden` (10⁴ frames, about 50 s); the cocotb harness runs about 6k cycles/s.
  After F3 (338 tests), with the builds reused, the test phase of `just ci` takes about 7 minutes on WSL. The longest
  entries are `tc_rx_csr_random` (about 70 s per run), `tc_feed_random` (2000 feed frames with resyncs and
  snapshots, about 55 s per run) and the `hft_csr_wrap` directed group (up to 46 s per profile); the MoldUDP64
  unit tests `tc_line_rate_b2b` and `tc_random_golden` (10⁴ packets) take about 35 s each. Right after a WSL
  restart (cold file cache) the same run took 4 minutes. A cold build of all F1 tests took 10 to 36 minutes on
  `/mnt/d`, mostly I/O wait; see Troubleshooting for a faster build directory.
  After F4 (464 tests) a full `just ci` takes about 15 minutes on WSL (test phase 14 minutes, builds reused). The
  longest entries are the `hft_csr_wrap` directed groups (80 to 130 s per profile), `tc_random_golden` of
  `eth_ip_udp_rx` (about 110 s), `tc_rx_csr_random`, `tc_feed_random` and `tc_itch_random` (2000 frames each, 75
  to 95 s per run); the ITCH unit test `tc_random_golden` (10⁵ messages) takes about 50 s. A change to
  `sva_core.f` or `core.f` rebuilds every core build: about 30 minutes for the whole suite on WSL.
- **Every run has a seed.** Each test prints a line such as
  `[hft] reset_sync seed=1302067203 build=reset_sync_33b5cad549 ... (rerun: HFT_SEED=1302067203)`.
  The seed drives cocotb's `random` and Verilator's randomized initial state (`--x-initial unique`): flops without a
  reset start with random values, so a missing reset shows up as a failure.
  To replay a failure exactly: `HFT_SEED=<seed> just test-one <name>`.
- **Failures carry their own diagnosis.** A failed cocotb run reports its seed, its environment, the log path
  (`build/sim/<build>/run_<env>/sim.log`, kept on disk) and the simulator's error lines, e.g. the failing SVA. This
  appears in the pytest summary and in `build/test-results.xml`. The report lists every failed cocotb test; the
  assertion and error details are capped at 12 lines (`HFT_LOG_LINES=<n>` to change it, or read `sim.log`).
- **No silent passes.** A cocotb run that passed still fails when:
  - it ran no test (a `testcase` filter that matches nothing);
  - a requested test did not run;
  - its log reports `Assertion failed` or `%Error` (`runner.run_problems`).
  
  `run_svtb` rejects a PASS line printed after such an error. Selftests check that:
  - every `tc_*` coroutine carries `@cocotb.test`;
  - every `LONG_TESTS` name exists;
  - every RTL and SVA file is in exactly one filelist.
  
  `just lint` fails on a missing filelist.
- **Assertions (SVA):** `firmware/filelists/sva.f` (protocol checkers in `firmware/tb/sva/` plus binds) is compiled
  into every cocotb build, and `sva_core.f` into every build that uses `core.f`. A failing assertion stops the
  simulation. The checkers themselves are tested in `tb/unit/test_sva_checkers.py`: an SV testbench drives each
  checker directly, stays quiet on legal traffic, and violates each property once. Inside a DUT test the cocotb
  checks often see a fault one edge before the assertion, and the next test's reset then disables it.
- **Parallel runs:** `just test` uses `pytest -n auto --dist loadgroup`.
  - The number of workers and of compile jobs comes from one budget: min(CPUs, available RAM / 600 MB).
    Under WSL the budget is capped at 2. Override it with `HFT_JOBS=<n>` (environment or `.env.local`).
  - A per-build file lock means a build is compiled once even if several workers need it.
  - If a test module passes some settings only as environment variables (for example the clock ratio), it lists them
    in `RUNTIME_PARAMS`, so all values share one build.
- **CDC jitter mode:** tests of clock-domain-crossing blocks also run with the `HFT_SIM_CDC_JITTER` define. In that
  mode, `sync_bit` captures bits that change close to a clock edge one cycle late, at random, which models
  metastability. Without it, a non-gray multi-bit crossing passes simulation; with it, the test fails.

### Test helpers (`firmware/tb/common/`)

| Module | Purpose |
|--------|---------|
| `runner.py` | `run_cocotb(toplevel, filelists, test_module, parameters, defines, extra_env, sva=True)` |
| `svtb.py` | `run_svtb(top, filelists, tb_files)`: self-checking SV testbench under Verilator `--binary --timing`. The TB must print one `HFT_TB_RESULT: PASS/FAIL` line |
| `clocks.py` | `start_clock(sig, period_ps, jitter_pct)`, plus clock-ratio profiles selected by `RATIO` (`same`, `net2host`, `host2net`, `jitter`) |
| `scoreboard.py` | In-order scoreboard (catches loss, duplication and reordering) |
| `coverage.py` | `Coverage(name, required_bins)`: `hit(bin)` (an unknown bin fails at once), `check(out_dir)` writes `coverage.json` and fails if a required bin is empty |
| `axil.py` | `AxilDriver`: AXI4-Lite manager for what cocotbext-axi does not do (W before AW, garbage after each handshake, random `bready`/`rready`, many requests in flight) |
| `csr_env.py` | `hft_csr_wrap` harness shared by its unit test and the system test: NET and FEED configurations and commits, the `cfg_*` atomicity monitors, resync requests (none lost, merged per transfer), the counter snapshot and its model (feed state included) |
| `itch_real.py` | Real Nasdaq data: `load()` the manifest, `fetch(src)` the range into `build/itch-real/` (SHA-256 checked, one download per source even with parallel workers), `messages(path)` and `packets(path)` (the MoldUDP64 packing every real-data test uses) |

### Generated CSR code (F5)

The register map has one source, [`firmware/rdl/hft_csr.rdl`](../firmware/rdl/hft_csr.rdl). `just gen` runs
PeakRDL through `scripts/gen_csr.sh` (the only place it is called; options in `firmware/rdl/peakrdl.toml`) and
writes `firmware/rtl/core/gen/`, `software/include/gen/hft_csr.h` and `firmware/rdl/hft_csr.md`. These files are
committed, so the Vivado flow on Windows needs no Python, and never edited by hand. `just gen-check` regenerates
into a temporary directory and fails on any difference: an RDL change without `just gen`, or a hand edit of a
generated file. The output is byte-identical on x86_64 and arm64.

To change the map: edit the RDL, `just gen`, update the hand-written address table in
`tb/selftest/test_csr_map.py` and `tb/selftest/csr_header_check.cpp` (they are independent of the RDL on purpose,
so an unintended shift fails), then `just fmt lint test`.

### Golden models (`firmware/tb/model/`) and coverage

- `csr.py` is the register model: it compiles the RDL with `systemrdl-compiler` (not the generated SV) and
  answers each access like the block should (read data, `OKAY`/`SLVERR`, `wstrb`, singlepulse fields).
- `net.py` is the reference for `eth_ip_udp_rx`, in pure Python. `expect(frame, tuser, cfg)` parses the frame with
  scapy and applies the receive rules in the documented priority order. It never uses the RTL's byte offsets, so
  the model and the DUT cannot share a byte-order mistake. Frame builders and `random_traffic()` produce the
  stimulus, each frame tagged with the outcome the generator meant.
- `tb/selftest/test_net_model.py` tests the model without a simulator (`just test-one net_model`):
  - hand-written hex frames whose checksums were summed by hand;
  - every pair of faults, for the priority order;
  - the reason codes against `hft_pkg.sv`;
  - that the generator's intent and the model agree on 2000 random frames.
- `mold.py` is the reference for `moldudp64_rx`, also pure Python. `expect(payload, abort, filter_idx, cfg, state)`
  applies the MoldUDP64 rules to the byte string, with no notion of beats or lanes. Packet builders, a hand-written
  ITCH 5.0 length table and `random_feed()` (round-robin categories, so coverage does not depend on the seed)
  produce the stimulus. `tb/selftest/test_mold_model.py` (`just test-one mold_model`) checks hand-written packets,
  every pair of faults for the priority, the status codes against `hft_pkg.sv`, the 2⁶⁴ wrap and the generator.
  The system test chains the two models: the F2 model's payload, abort and filter index go into `mold.expect`.
- `itch.py` is the reference for `itch_decoder`, also pure Python: the 23 TotalView-ITCH 5.0 types written out from
  the specification (a page number on every line), `encode`/`decode`, `classify` and `expect(msg, cfg, bitmap)`
  (class, outcome and the normalized event). `itch_stream()`/`random_itch()` produce real messages in 106
  round-robin categories; `mold.random_feed(..., msg_source=...)` packs them into MoldUDP64 packets.
  `tb/selftest/test_itch_model.py` (`just test-one itch_model`) checks hand-written hex messages for every type,
  the class, outcome and type codes against `hft_pkg.sv`, the length table against `mold.py` and the field layout.
  The system test chains all three models (F2 → F3 → F4).
- **Coverage is a gate.** `tc_random_golden` hits named bins (reasons, last-beat cases, MAC classes, filter
  entries, back-to-back pairs, gaps) and fails if a required bin stays empty. The MoldUDP64 and ITCH
  `tc_random_golden`, `tc_rx_csr_random`, `tc_feed_random` and `tc_itch_random` have their own gates. The counts are written to
  `coverage.json` in the test's run directory. Bins are not removed to make a test pass.

### Real Nasdaq data (`firmware/tb/data/itch_real.toml`)

The real-data tests (marker `real`) read the start of real trading days from Nasdaq's public TotalView-ITCH 5.0
samples. The files carry no license to redistribute them, so the data never enters the repository, a CI cache or an
artifact. The manifest names, per source, the URL, a byte range of the compressed file, the SHA-256 of that range,
the expected results (`expect`) and the windows of the system test (`system`). `fetch()` downloads the range with an
HTTP Range request and checks the hash; the file stays in `build/itch-real/` while its hash matches. A hash that does
not match after a fresh download means the file on the server changed, and the test fails.

- `just test` deselects the marker (`-m 'not real'` in `pyproject.toml`), so it never needs the network.
- `just itch-real` (`firmware/tb/real/`): the ITCH and Mold models on every message, with order-book consistency
  checks. About 2.5 minutes on WSL with the data downloaded; CI job `itch-real` on every push (about 2 minutes).
- `just itch-real-rtl` (`tc_real_itch` of `test_itch_decoder` and `test_rx_csr`): `itch_decoder` on every message
  of each range at line rate, and the system test on the `system` windows (the whole range would take hours
  there). On the GitHub runners the four run in parallel: `itch_decoder` takes 25 (arm64) to 42 (x64) minutes per
  source and the system windows 4 to 9 minutes, so a whole run takes 26 to 43 minutes. On WSL the system windows
  take 6 to 7 minutes and `itch_decoder` runs at about 2400 messages per second (about 75 minutes per source). Run it once per phase: Actions > itch-real-rtl > Run workflow (a workflow
  of its own, so pushes do not cancel it).

Adding a source:
1. `python firmware/tb/common/itch_real.py URL LAST` downloads bytes 0..LAST of the file and prints the `range` and
   `sha256` lines. The range must start at 0 (the gzip header).
2. Add a `[[source]]` entry with those lines and `status = "active"`, then run `just itch-real`: the new source
   fails and prints its measured `[source.expect]`. Check the numbers, then paste them.
3. Choose the `system` windows (`[first packet, packets]`): the start of the day, then a window at an open.

Retiring a source (its URL is gone or its file changed): set `status = "retired"` and a `note` with the date and the
reason, and keep the entry with its hash and results as the record; a retired source is never downloaded and its
tests skip with the note. Then add a new source as above and update the table in the README.

AXI/AXI-Stream drivers come from [cocotbext-axi](https://github.com/alexforencich/cocotbext-axi). It works with
cocotb 2.1 but emits `DeprecationWarning`s.

Open waveforms with GTKWave or Surfer. Both ship with OSS CAD Suite and open through WSLg, for example
`.tools/oss-cad-suite/bin/surfer build/sim/.../dump.fst`. Any Windows viewer works too.

### Adding a module

1. Add `firmware/rtl/<area>/<name>.sv` (file name = module name) and list it in a filelist under `firmware/filelists/`.
2. Add `firmware/tb/unit/test_<name>.py` (or `tb/system/` for a top joining several blocks) with `tc_*` cocotb
   coroutines and a `test_<name>()` pytest entry point that calls `common.runner.run_cocotb`.
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

Vivado is not needed for lint or tests. It is used for the synthesis gates and timing/utilization reports, and to
run the cross-simulator FIFO testbench. Simulation runs anywhere (also in GitHub Actions); the synthesis gates run
only on a machine with Vivado. The reference part of the core and CSR gates needs the Kintex UltraScale+ device files: add
them with the installer's "Add Design Tools or Devices" and check with `get_parts xcku5p-ffvb676-2-e` in the Tcl
console.

```powershell
just vivado-project                      # build/vivado/xc7a100tcsg324-1/hft.xpr (no board part)
just synth-ooc cdc_handshake host2net     # build/vivado/ooc/<top>_<profile>_<part>/{timing_summary,utilization,cdc}.rpt
just synth-ooc cdc_async_fifo_ooc net2host IMPL:xpm   # generics as NAME:VALUE
just synth-ooc-on xcku5p-ffvb676-2-e eth_ip_udp_rx_ooc net2host core.f synth_ooc_core.f   # another part
just synth-common                         # every common block, both clock directions, gated
just synth-core                           # eth_ip_udp_rx and moldudp64_rx placed and routed at 156.25 MHz: A7 and xcku5p
just synth-csr                            # hft_csr_wrap placed and routed: xcku5p 156.25/250 MHz, A7 156.25/125 MHz (about 15 min)
just xsim-test xpm 1                     # cdc_async_fifo equivalence TB in XSIM (portable | xpm, seed)
```

`just xsim-test` runs `firmware/tb/sv/tb_cdc_fifo_equiv.sv`. It is the same self-checking TB that `just test` runs
under Verilator (seeds 1–3). The TB uses its own random generator, so for the portable FIFO a given seed produces
identical results in both simulators. The XPM variant (`IMPL=xpm`) can only be simulated in Vivado. It is checked
against the same contract and stimulus; its FWFT capacity is one word larger.

`synth-ooc` (and therefore every `synth-*` gate) **fails** in any of these cases:
- negative register-to-register setup or hold slack (after routing when the `impl` flag is given);
- a port that the port-to-clock map does not clock (except the asynchronous `d` and `rst_in`): its crossings would
  be missing from `report_cdc`;
- with `impl`, a net that `route_design` left unrouted;
- a Critical `report_cdc` finding, or any warning ID other than CDC-6 (gray-coded multi-bit synchronizer) and CDC-15
  (enable-captured stable data);
- a crossing that `firmware/constraints/cdc.tcl` could not constrain.

`cdc.tcl` derives the CDC constraints from the netlist rather than from instance names:
- the first flop of each `ASYNC_REG` chain, plus bus skew per synchronizer;
- cells marked `(* hft_cdc_data = "TRUE" *)`;
- LUTRAM read across clocks (FIFO storage).

Clock profiles: `net2host` is 156.25 MHz → 250 MHz (`wr`/`src`/`clk` at 156.25), `host2net` is the reverse, and
`net2host125` has a 125 MHz host. The flag `impl` (as an extra argument) also runs `opt_design`, `place_design` and
`route_design` out of context: the gate then uses the routed timing, and the synthesis estimate is printed as
`SYNTH_WNS`. The estimate can be about 1 ns too optimistic (it missed a failing read path on the A7 in F5).
Cell counts and the LUTRAM rule of `cdc.tcl` cover both 7-series and UltraScale+ primitive names.
The legacy top also synthesizes, but its port names are not in the port-to-clock map, so its CDC check is only partial:
`just synth-ooc HFT_Top_Core net2host legacy.f`.

The project file is generated and never committed. Sources come from the same filelists that Verilator uses.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `fatal: detected dubious ownership` in WSL | `git config --global --add safe.directory /mnt/d/<path-to-repo>` |
| `just: command not found` in WSL | Use a login shell (`bash -l`) or add `~/.local/bin` to `PATH` |
| `Permission denied` running a recipe script | `XDG_RUNTIME_DIR` is `noexec`; script recipes already use `[script("bash")]`. Keep it that way for new ones |
| A long background job in WSL dies when the last `wsl.exe` session closes | WSL stops the distro and its processes; `setsid`/`nohup` do not help. Keep a `wsl` session open for the job's whole run |
| `Wsl/Service/E_UNEXPECTED` during `just test` | The Windows host could not supply the memory the WSL VM asked for during parallel builds. Cap the VM in `%USERPROFILE%\.wslconfig` (`[wsl2]` `memory=3GB`, `swap=4GB`), run `wsl --shutdown`, and/or set `HFT_JOBS=1` |
| `.venv/bin/python` disappears, builds fail with missing `cocotb/share/...` files | Something on the Windows side touched the repo's `.venv` (it is shared through `/mnt/d`). On WSL set `UV_PROJECT_ENVIRONMENT=/home/<you>/.cache/hft-feedhandler/venv` in `.env.local` and run `just setup`; the venv then lives on ext4, out of Windows' reach |
| `SRE module mismatch` in a build log | A Verilator helper (`verilator_includer`) ran with another Python than the venv's stdlib on `PYTHONPATH`. The runner sets `PYTHON3` for make to the venv interpreter; keep that when changing the runner |
| Slow first build on `/mnt/d` | Exclude the repo's `build/` and `.tools/` folders from Windows Defender real-time scanning, or set `HFT_BUILD_DIR` to a WSL path |
| Lint does not flag a signal named `*unused*` | Verilator ignores names matching `--unused-regexp` (`*unused*`) on purpose |
| `Unsupported arch` | 32-bit Pi OS is not supported. Install the 64-bit image |
| Wrong `python` inside recipes | Do not source OSS CAD Suite's `environment` script; the justfile only adds its `bin/` to `PATH` |
| Verilator too old or broken | `scripts/build-verilator.sh [tag]` builds a pinned Verilator into `.tools/verilator` |
| XSIM `compile.bat is not recognized` | The environment variable `NoDefaultCurrentDirectoryInExePath` is set; unset it for Vivado |
