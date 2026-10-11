"""Simulator-free self-tests of the shared test helpers."""

import pytest

from common.clocks import PROFILES, profile
from common.runner import build_key, env_dir_name
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
