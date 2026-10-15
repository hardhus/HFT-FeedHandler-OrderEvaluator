"""Functional coverage as a test gate: named bins with hit counts (09 §2.12).

A test declares its required bins up front, calls hit() as it observes events and
check() at the end. check() writes the counts to <out_dir>/coverage.json (one entry
per Coverage name, so several groups can share a run directory) and fails if any
required bin is empty. Unknown bin names fail immediately, so a typo cannot hide
a hole. Remove a bin only with a written reason, never to make a test pass.
"""

import json
from collections.abc import Iterable
from pathlib import Path

REPORT = "coverage.json"


class Coverage:
    """Named bins with hit counts; required bins must all be hit (else AssertionError)."""

    def __init__(self, name: str, required: Iterable[str]) -> None:
        self.name = name
        self.bins: dict[str, int] = {}
        for b in required:
            assert b not in self.bins, f"{name}: duplicate bin {b!r}"
            self.bins[b] = 0
        assert self.bins, f"{name}: no bins"

    def hit(self, bin_name: str) -> None:
        assert bin_name in self.bins, f"{self.name}: unknown bin {bin_name!r}"
        self.bins[bin_name] += 1

    def empty(self) -> list[str]:
        return [b for b, n in self.bins.items() if n == 0]

    def check(self, out_dir: Path) -> None:
        """Write the report (also on failure), then assert that no required bin is empty."""
        path = Path(out_dir) / REPORT
        report = json.loads(path.read_text()) if path.exists() else {}
        report[self.name] = {"bins": self.bins, "empty": self.empty()}
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        missing = self.empty()
        assert not missing, f"{self.name}: {len(missing)}/{len(self.bins)} required bin(s) empty: {missing}"
