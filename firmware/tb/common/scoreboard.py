"""In-order scoreboard: catches loss, duplication and reordering of transactions."""

from collections import deque
from typing import Any


class Scoreboard:
    def __init__(self, name: str) -> None:
        self.name = name
        self.expected: deque[Any] = deque()
        self.pushed = 0
        self.checked = 0

    def push(self, item: Any) -> None:
        """Record an item that must come out later, in this order."""
        self.expected.append(item)
        self.pushed += 1

    def pop_check(self, actual: Any) -> None:
        """Compare an observed item with the oldest expected one."""
        assert self.expected, f"{self.name}: unexpected item #{self.checked}: {_fmt(actual)} (nothing expected)"
        want = self.expected.popleft()
        assert actual == want, f"{self.name}: item #{self.checked}: expected {_fmt(want)}, got {_fmt(actual)}"
        self.checked += 1

    def check_empty(self) -> None:
        """Call at the end of a test: every expected item must have been observed."""
        missing = len(self.expected)
        head = ", ".join(_fmt(x) for x in list(self.expected)[:4])
        assert missing == 0, f"{self.name}: {missing} expected item(s) never observed (first: {head})"


def _fmt(x: Any) -> str:
    return f"0x{x:x}" if isinstance(x, int) else repr(x)
