"""AXI4-Lite manager that does what the standard allows and cocotbext-axi's AxiLiteMaster does not
(audit/10 §2.8): W before, with or after AW (per-transfer delays), random garbage on AW/W/AR as soon
as the handshake is done (legacy K6/K8 used the bus after it), BREADY/RREADY held low at random
(K7/K8 lost responses under backpressure) and many requests in flight.

Each channel is its own coroutine. A transfer is presented right after a rising edge; the handshake
is decided in ReadOnly of the same cycle (VALID and READY both 1 before the edge), so there is no
read-after-edge race. Responses come back in request order (AXI4-Lite has no IDs) and resolve the
Pending objects of the requests. The driver has no self-test of its own: axil_props (SVA) and the
register-model scoreboard of the tests check it together with the DUT.

reset(rst): the test calls it when it asserts the subordinate's reset. Requests not yet answered
are dropped (Pending.dropped, never done), queued ones discarded, a transfer being presented is
withdrawn, and the bookkeeping forgets them. Until rst has been seen high and then low on clock
edges the driver presents nothing and counts no handshake (a transfer on the edge that resets the
subordinate is void). After that, a response with no request fails ("response without a
request"), so a stale answer after a reset is caught. test_hft_csr.tc_reset_mid_traffic checks the
driver's recovery.
"""

import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import cocotb
from cocotb.triggers import Event, ReadOnly, RisingEdge
from cocotb.utils import get_sim_time


@dataclass
class Pending:
    """A request in flight; `result` is set when its response is accepted."""

    kind: str  # "w" or "r"
    addr: int
    data: int = 0
    strb: int = 0xF
    result: tuple[int, int] | None = None  # (rdata or 0, xRESP)
    done: Event = field(default_factory=Event)
    dropped: bool = False  # the subordinate was reset before it answered (see AxilDriver.reset)

    async def wait(self) -> tuple[int, int]:
        await self.done.wait()
        assert self.result is not None
        return self.result


class AxilDriver:
    """Drives <prefix>_aw*/w*/b*/ar*/r*. Start it right after a rising edge, after reset."""

    def __init__(self, dut, clk, prefix: str = "s_axil", addr_w: int = 16, rng: random.Random | None = None):
        self.sig = lambda name: getattr(dut, f"{prefix}_{name}")
        self.clk = clk
        self.addr_w = addr_w
        self.rng = rng or random.Random(random.getrandbits(32))
        self.aw_q: deque[tuple[int, dict[str, int]]] = deque()
        self.w_q: deque[tuple[int, dict[str, int]]] = deque()
        self.ar_q: deque[tuple[int, dict[str, int]]] = deque()
        self.b_wait: deque[Pending] = deque()
        self.r_wait: deque[Pending] = deque()
        # Probability of BREADY/RREADY in a cycle; tests change them at any time.
        self.b_ready_p = 1.0
        self.r_ready_p = 1.0
        # Observations for checks and coverage.
        self.hs_time: dict[str, list[int]] = {ch: [] for ch in ("aw", "w", "b", "ar", "r")}
        self.stalled = {"b": 0, "r": 0}  # cycles with VALID and not READY
        self.max_outstanding = {"w": 0, "r": 0}
        self.epoch = 0  # incremented by reset(): transfers of an older epoch are withdrawn
        self.quiet = False  # between reset() and the release of the subordinate's reset
        self.lost = {"w": 0, "r": 0}  # accepted requests that a reset left without a response
        for ch in ("aw", "w", "ar"):
            self.sig(f"{ch}valid").value = 0
            self._garbage(ch)
        self.sig("bready").value = 0
        self.sig("rready").value = 0
        cocotb.start_soon(self._request(self.aw_q, "aw"))
        cocotb.start_soon(self._request(self.w_q, "w"))
        cocotb.start_soon(self._request(self.ar_q, "ar"))
        cocotb.start_soon(self._response("b", self.b_wait, lambda: self.b_ready_p, ("bresp",)))
        cocotb.start_soon(self._response("r", self.r_wait, lambda: self.r_ready_p, ("rdata", "rresp")))

    # ---- requests ----

    def write_nowait(self, addr: int, data: int, strb: int = 0xF, aw_delay: int = 0, w_delay: int = 0) -> Pending:
        """Queue a write. aw_delay/w_delay: idle cycles before each channel presents it (after the
        previous transfer of that channel), so w_delay < aw_delay sends W first."""
        p = Pending("w", addr, data, strb)
        self.aw_q.append((aw_delay, {"awaddr": addr, "awprot": 0}))
        self.w_q.append((w_delay, {"wdata": data, "wstrb": strb}))
        self.b_wait.append(p)
        return p

    def read_nowait(self, addr: int, delay: int = 0) -> Pending:
        p = Pending("r", addr)
        self.ar_q.append((delay, {"araddr": addr, "arprot": 0}))
        self.r_wait.append(p)
        return p

    async def write(self, addr: int, data: int, strb: int = 0xF, aw_delay: int = 0, w_delay: int = 0) -> int:
        return (await self.write_nowait(addr, data, strb, aw_delay, w_delay).wait())[1]

    async def read(self, addr: int, delay: int = 0) -> tuple[int, int]:
        return await self.read_nowait(addr, delay).wait()

    async def idle(self) -> None:
        """Wait until every queued request has its response."""
        while self.b_wait or self.r_wait:
            await RisingEdge(self.clk)

    def reset(self, rst) -> None:
        """The subordinate is being reset (call it when `rst` is driven high): drop every request
        without a response, discard the queued ones, withdraw the one being presented, and stay
        quiet until `rst` has been high and then low on clock edges."""
        self.epoch += 1
        self.quiet = True
        cocotb.start_soon(self._wait_release(rst, self.epoch))
        for q in (self.aw_q, self.w_q, self.ar_q):
            q.clear()
        for pend in (*self.b_wait, *self.r_wait):
            pend.dropped = True
        # A write with only one of its AW/W handshakes is gone: keep the two lists paired.
        n = min(len(self.hs_time["aw"]), len(self.hs_time["w"]))
        del self.hs_time["aw"][n:], self.hs_time["w"][n:]
        self.lost["w"] = n - len(self.hs_time["b"])
        self.lost["r"] = len(self.hs_time["ar"]) - len(self.hs_time["r"])
        self.b_wait.clear()
        self.r_wait.clear()

    async def _wait_release(self, rst, epoch: int) -> None:
        seen = False
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            if epoch != self.epoch:  # a newer reset() took over
                return
            seen = seen or bool(rst.value)
            if seen and not rst.value:
                break
        await RisingEdge(self.clk)
        if epoch == self.epoch:
            self.quiet = False

    def write_orders(self) -> list[str]:
        """For each completed write: which of its AW and W handshakes came first."""
        out = []
        for aw, w in zip(self.hs_time["aw"], self.hs_time["w"]):
            out.append("same" if aw == w else "aw_first" if aw < w else "w_first")
        return out

    # ---- channel coroutines ----

    def _garbage(self, ch: str) -> None:
        if ch == "aw":
            self.sig("awaddr").value = self.rng.getrandbits(self.addr_w)
            self.sig("awprot").value = self.rng.getrandbits(3)
        elif ch == "w":
            self.sig("wdata").value = self.rng.getrandbits(32)
            self.sig("wstrb").value = self.rng.getrandbits(4)
        else:
            self.sig("araddr").value = self.rng.getrandbits(self.addr_w)
            self.sig("arprot").value = self.rng.getrandbits(3)

    async def _request(self, queue: deque, ch: str) -> None:
        valid, ready = self.sig(f"{ch}valid"), self.sig(f"{ch}ready")
        await RisingEdge(self.clk)
        while True:
            if not queue or self.quiet:
                self._garbage(ch)
                await RisingEdge(self.clk)
                continue
            delay, values = queue.popleft()
            epoch = self.epoch
            for _ in range(delay):
                self._garbage(ch)
                await RisingEdge(self.clk)
            if epoch != self.epoch:  # reset while it waited: dropped
                continue
            for name, value in values.items():
                self.sig(name).value = value
            valid.value = 1
            while True:
                await ReadOnly()
                accepted = bool(ready.value)
                await RisingEdge(self.clk)
                if accepted or epoch != self.epoch:
                    break
            valid.value = 0
            self._garbage(ch)  # the subordinate must have stored the payload (K6/K8)
            if epoch == self.epoch:
                self.hs_time[ch].append(get_sim_time())

    async def _response(self, ch: str, waiting: deque, ready_p: Callable[[], float], names: tuple[str, ...]) -> None:
        valid, ready = self.sig(f"{ch}valid"), self.sig(f"{ch}ready")
        await RisingEdge(self.clk)
        while True:
            ready.value = int(self.rng.random() < ready_p())
            await ReadOnly()
            v, r = bool(valid.value), bool(ready.value)
            values = tuple(int(self.sig(n).value) for n in names)
            epoch = self.epoch
            await RisingEdge(self.clk)
            if epoch != self.epoch or self.quiet:  # around a reset: void
                continue
            req = "aw" if ch == "b" else "ar"
            kind = "w" if ch == "b" else "r"
            done = len(self.hs_time[ch]) + self.lost[kind]
            if ch == "b":
                accepted = min(len(self.hs_time["aw"]), len(self.hs_time["w"]))
            else:
                accepted = len(self.hs_time[req])
            self.max_outstanding[kind] = max(self.max_outstanding[kind], accepted - done)
            if v and not r:
                self.stalled[ch] += 1
            if v and r:
                assert waiting, f"axil: {ch.upper()} response without a request"
                p = waiting.popleft()
                p.result = (0, values[0]) if ch == "b" else (values[0], values[1])
                self.hs_time[ch].append(get_sim_time())
                p.done.set()
