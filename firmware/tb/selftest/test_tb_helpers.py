"""Simulator-free self-tests of the shared test helpers."""

import ast
import json
import re
from pathlib import Path

import pytest

from common.clocks import PROFILES, profile
from common.coverage import REPORT, Coverage
from common.filelist import read_filelist
from common.junit import tidy_junit
from common.runner import (
    FILELISTS,
    REPO,
    WAIVERS,
    build_key,
    env_dir_name,
    failure_lines,
    run_problems,
    sim_filelists,
    sources_for,
)
from common.scoreboard import Scoreboard


def test_scoreboard_in_order():
    sb = Scoreboard("t")
    for x in (1, 2, 3):
        sb.push(x)
    for x in (1, 2, 3):
        sb.pop_check(x)
    sb.check_empty()


@pytest.mark.parametrize(
    "pushed, observed",
    [([1, 2], [2]), ([1], [1, 1]), ([1, 2], [1])],
    ids=["reorder", "duplicate", "loss"],
)
def test_scoreboard_detects_errors(pushed, observed):
    sb = Scoreboard("t")
    for x in pushed:
        sb.push(x)
    with pytest.raises(AssertionError):
        for x in observed:
            sb.pop_check(x)
        sb.check_empty()


def test_build_key_depends_on_config_and_sources(tmp_path):
    src = tmp_path / "a.sv"
    src.write_text("module a; endmodule\n")
    k1 = build_key("a", {"p": 1}, [src])
    assert k1 == build_key("a", {"p": 1}, [src])
    assert k1 != build_key("a", {"p": 2}, [src])
    src.write_text("module a; logic x; endmodule\n")
    assert k1 != build_key("a", {"p": 1}, [src])


def test_env_dir_name_is_order_independent():
    assert env_dir_name({"A": "1", "B": "2"}) == env_dir_name({"B": "2", "A": "1"})
    assert env_dir_name({"A": "1"}) != env_dir_name({"A": "2"})


def test_clock_profiles(monkeypatch):
    monkeypatch.setenv("RATIO", "net2host")
    assert profile() == PROFILES["net2host"]
    assert profile("same")[0] == profile("same")[1]


def test_tidy_junit(tmp_path):
    report = tmp_path / "r.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="2">'
        '<testcase classname="m" name="t[b]@m::t[x=1]" time="1" />'
        '<testcase classname="m" name="t[a]@m::t[x=1]" time="2"><failure message="boom" /></testcase>'
        "</testsuite></testsuites>"
    )
    tidy_junit(report)
    text = report.read_text()
    lines = text.splitlines()
    assert len(lines) > 4, "report should be indented, one element per line"
    assert "@" not in text, "xdist group suffix should be removed"
    assert text.index('name="t[a]"') < text.index('name="t[b]"'), "cases should be sorted"
    assert '<failure message="boom"' in text, "failure details must be kept"


def test_sim_filelists_adds_sva_partners():
    assert sim_filelists(["common.f", "core.f"], sva=True) == ["common.f", "core.f", "sva.f", "sva_core.f"]
    assert sim_filelists(["common.f"], sva=True) == ["common.f", "sva.f"], "F1 builds must not see sva_core.f"
    assert sim_filelists(["common.f", "core.f"], sva=False) == ["common.f", "core.f"]
    assert sim_filelists(["common.f", "core.f", "csr.f"], sva=True) == [
        "common.f", "core.f", "csr.f", "sva.f", "sva_core.f", "sva_csr.f"
    ]  # fmt: skip


def test_waivers_only_in_builds_with_core_or_generated_rtl():
    """Builds with hft_pkg (core.f) or generated RTL (csr.f) get the Verilator waiver file, once;
    the common-block (F1) builds do not, so a new waiver does not rebuild them."""
    csr = sources_for(["common.f", "core.f", "csr.f"])
    assert csr[0] == WAIVERS and csr.count(WAIVERS) == 1
    assert sources_for(["common.f", "core.f"])[0] == WAIVERS
    assert WAIVERS not in sources_for(["common.f"])


def test_coverage_empty_required_bin_fails(tmp_path):
    cov = Coverage("g", ["a", "b"])
    cov.hit("a")
    with pytest.raises(AssertionError, match="'b'"):
        cov.check(tmp_path)
    assert json.loads((tmp_path / REPORT).read_text())["g"]["empty"] == ["b"], "report is written on failure too"


def test_coverage_unknown_bin_fails():
    cov = Coverage("g", ["a"])
    with pytest.raises(AssertionError, match="unknown bin"):
        cov.hit("A")


def test_coverage_report(tmp_path):
    first = Coverage("first", ["x"])
    first.hit("x")
    first.check(tmp_path)
    second = Coverage("second", ["y", "z"])
    for b in ("y", "y", "z"):
        second.hit(b)
    second.check(tmp_path)
    report = json.loads((tmp_path / REPORT).read_text())
    assert report == {
        "first": {"bins": {"x": 1}, "empty": []},
        "second": {"bins": {"y": 2, "z": 1}, "empty": []},
    }, "groups sharing a run directory keep separate entries"


def test_failure_lines_lists_every_failed_test(tmp_path, monkeypatch):
    """Regression: the report kept only the first 12 matching lines, so with many failing
    tests the list of failed tests was cut (seen in the F2 S3 mutation runs)."""
    log = tmp_path / "sim.log"
    body = []
    for i in range(20):
        body += [f"{i}.00ns WARNING  cocotb.regression  test_m.tc_{i} failed", "Traceback:", f"AssertionError: detail {i}"]
    log.write_text("\n".join(body) + "\n")
    out = failure_lines(log, limit=3).splitlines()
    assert [line for line in out if "cocotb.regression" in line] == [line for line in body if "regression" in line]
    assert sum("AssertionError" in line for line in out) == 3, "details stay bounded"
    monkeypatch.setenv("HFT_LOG_LINES", "5")
    assert sum("AssertionError" in line for line in failure_lines(log).splitlines()) == 5


def _results_xml(tmp_path, names):
    cases = "".join(f'<testcase classname="m" name="{n}" time="0" />' for n in names)
    path = tmp_path / "results.xml"
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite name="m">{cases}</testsuite></testsuites>')
    return path


def test_run_problems_catches_silent_passes(tmp_path):
    """Regression (verification hardening): a cocotb run with a testcase filter that matches
    nothing ran zero tests and still passed."""
    log = tmp_path / "sim.log"
    log.write_text("INFO all good\n")
    assert run_problems(_results_xml(tmp_path, ["tc_a", "tc_b"]), log, ["tc_a"]) == []
    assert "no cocotb test ran" in run_problems(_results_xml(tmp_path, []), log, None)
    assert any("did not run" in p for p in run_problems(_results_xml(tmp_path, ["tc_a"]), log, ["tc_a", "tc_x"]))
    assert any("did not run" in p for p in run_problems(_results_xml(tmp_path, ["tc_a"]), log, "tc_typo"))
    log.write_text("[100] %Error: x.sv:3: Assertion failed in top.a_check\n")
    assert any("reports errors" in p for p in run_problems(_results_xml(tmp_path, ["tc_a"]), log, None))
    assert any("no readable" in p for p in run_problems(tmp_path / "missing.xml", log, None))


def _unit_modules():
    """cocotb test modules: unit tests and system tests."""
    tb = REPO / "firmware" / "tb"
    return sorted([*(tb / "unit").glob("test_*.py"), *(tb / "system").glob("test_*.py")])


def test_every_tc_is_a_cocotb_test():
    """A tc_* coroutine without @cocotb.test is never run and nothing would say so."""
    bare = []
    for path in _unit_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("tc_"):
                decorators = [ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list]
                if "cocotb.test" not in decorators:
                    bare.append(f"{path.name}:{node.lineno} {node.name}")
    assert not bare, f"tc_* without @cocotb.test: {bare}"


def test_long_test_groups_name_real_tests():
    """Every name in a module's LONG_TESTS is a tc_* defined in that module (a typo would make
    its pytest entry run nothing; run_problems also catches it at run time)."""
    for path in _unit_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "LONG_TESTS" for t in node.targets):
                names = ast.literal_eval(node.value)
                assert set(names) <= defined, f"{path.name}: LONG_TESTS names no test: {set(names) - defined}"


def test_every_rtl_and_sva_file_is_listed_once():
    """An RTL or SVA file outside every filelist is neither linted nor simulated."""
    fw = REPO / "firmware"
    listed: dict[Path, list[str]] = {}
    lists = ("common.f", "core.f", "csr.f", "system.f", "xilinx.f", "synth_ooc.f", "synth_ooc_core.f",
             "synth_ooc_csr.f", "sva.f", "sva_core.f", "sva_csr.f")  # fmt: skip
    for name in lists:
        for path in read_filelist(FILELISTS / name):
            listed.setdefault(path, []).append(name)
    files = [p.resolve() for area in ("common", "core", "platform") for p in (fw / "rtl" / area).rglob("*.sv")]
    files += [p.resolve() for d in ("sva", "system") for p in (fw / "tb" / d).glob("*.sv")]
    unlisted = [str(p.relative_to(REPO)) for p in files if p not in listed]
    twice = {str(p.relative_to(REPO)): lists for p, lists in listed.items() if len(lists) > 1}
    assert not unlisted, f"not in any filelist: {unlisted}"
    assert not twice, f"in more than one filelist: {twice}"
    missing = [str(p) for p in listed if not p.exists()]
    assert not missing, f"filelist entries that do not exist: {missing}"


def strip_sv_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def unused_package_constants(pkg: Path, users: list[Path]) -> list[str]:
    """localparams of `pkg` that no file in `users` names (whole word, comments ignored)."""
    names = re.findall(r"\blocalparam\b[^=;]*?\b(\w+)\s*=", strip_sv_comments(pkg.read_text()))
    text = "\n".join(strip_sv_comments(u.read_text()) for u in users)
    return [n for n in names if not re.search(rf"\b{n}\b", text)]


def test_every_package_constant_has_a_user():
    """CODING_STYLE: every hft_pkg constant has a user. Verilator cannot check this per top (one
    top uses only some of them, so UNUSEDPARAM is waived for hft_pkg in waivers.vlt); this checks
    it across all hand-written RTL instead."""
    fw = REPO / "firmware" / "rtl"
    pkg = fw / "core" / "hft_pkg.sv"
    users = [p for area in ("common", "core", "platform") for p in (fw / area).rglob("*.sv") if p != pkg]
    assert unused_package_constants(pkg, users) == []


def test_unused_package_constant_is_reported(tmp_path):
    """The check bites: a constant named only in a comment or in its own package is unused."""
    pkg = tmp_path / "p_pkg.sv"
    pkg.write_text(
        "package p_pkg;\n  localparam int unsigned A = 1;  // used\n  localparam logic [7:0] B_X = 8'h2;\n"
        "  localparam int unsigned C = B_X + 1;\n  /* localparam int unsigned D = 4; */\nendpackage\n"
    )
    user = tmp_path / "m.sv"
    user.write_text("module m; // C\n  logic [A-1:0] x;  /* C */\nendmodule\n")
    assert unused_package_constants(pkg, [user]) == ["B_X", "C"]
