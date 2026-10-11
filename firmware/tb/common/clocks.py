"""Clock helpers: fixed-period or jittered clocks and named clock-ratio profiles.

The clock ratio is a runtime setting (env RATIO), not an HDL parameter, so one
build can be exercised under every profile.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer

# name -> (first/write/source clock period ps, second/read/destination clock period ps, jitter %)
PROFILES: dict[str, tuple[int, int, int]] = {
    "same": (6400, 6400, 0),
    "net2host": (6400, 4000, 0),  # 156.25 MHz -> 250 MHz
    "host2net": (4000, 6400, 0),  # 250 MHz -> 156.25 MHz
    "jitter": (6400, 4000, 20),  # both clocks drift +/-20 % per half period
}


def profile(name: str | None = None) -> tuple[int, int, int]:
    """Profile `name`, or the one selected by env RATIO (default "same")."""
    return PROFILES[name or os.environ.get("RATIO", "same")]


async def _jittered(sig, period_ps: int, jitter_pct: int, rng: random.Random) -> None:
    half = period_ps // 2
    span = half * jitter_pct // 100
    while True:
        sig.value = 1
        await Timer(half + rng.randint(-span, span), unit="ps")
        sig.value = 0
        await Timer(half + rng.randint(-span, span), unit="ps")


def start_clock(sig, period_ps: int, jitter_pct: int = 0, seed: int | None = None):
    """Start a clock on `sig`. With jitter, each half period varies by +/-jitter_pct %."""
    if jitter_pct == 0:
        return Clock(sig, period_ps, unit="ps").start()
    rng = random.Random(seed if seed is not None else random.getrandbits(32))
    return cocotb.start_soon(_jittered(sig, period_ps, jitter_pct, rng))
