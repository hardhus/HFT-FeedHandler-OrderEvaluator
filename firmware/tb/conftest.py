"""pytest hooks shared by all firmware tests."""

import pytest


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
        runtime = set(getattr(item.module, "RUNTIME_PARAMS", ()))
        params = getattr(getattr(item, "callspec", None), "params", {})
        build_params = ",".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in runtime)
        item.add_marker(pytest.mark.xdist_group(f"{item.module.__name__}::{item.originalname}[{build_params}]"))
