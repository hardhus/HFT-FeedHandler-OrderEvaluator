"""Simulator-free self-tests of the shared test helpers."""

import json

import pytest

from common.clocks import PROFILES, profile
from common.coverage import REPORT, Coverage
from common.junit import tidy_junit
from common.runner import build_key, env_dir_name, sim_filelists
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
