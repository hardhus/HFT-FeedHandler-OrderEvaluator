"""pytest hooks shared by all firmware tests."""

from pathlib import Path

import pytest

from common.junit import tidy_junit
from common.runner import job_budget


@pytest.hookimpl(optionalhook=True)  # only exists when pytest-xdist is active
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    """`-n auto`: as many workers as CPUs and available memory allow (see runner.job_budget)."""
    return job_budget()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "xdist_group(name): run on the same pytest-xdist worker")


@pytest.hookimpl(tryfirst=True)  # before pytest-xdist reads the xdist_group markers
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep tests that share a simulator build on one xdist worker (--dist loadgroup).

    The group is the test function plus its parametrization, minus the parameters a
    test module lists in RUNTIME_PARAMS (settings passed via env such as the clock
    ratio, which do not change the build). Two workers therefore never compile the
    same build directory at the same time.
    """
    for item in items:
        if not isinstance(item, pytest.Function) or item.get_closest_marker("xdist_group"):
            continue
        if not hasattr(item, "callspec"):
            continue  # not parametrized: nothing to share, no group needed
        runtime = set(getattr(item.module, "RUNTIME_PARAMS", ()))
        params = item.callspec.params
        build_params = ",".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in runtime)
        item.add_marker(pytest.mark.xdist_group(f"{item.module.__name__}::{item.originalname}[{build_params}]"))


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """After the JUnit report is written (session finish), make it readable."""
    if hasattr(config, "workerinput"):  # pytest-xdist worker: the controller writes the report
        return
    xmlpath = getattr(config.option, "xmlpath", None)
    if xmlpath and Path(xmlpath).is_file():
        tidy_junit(Path(xmlpath))
