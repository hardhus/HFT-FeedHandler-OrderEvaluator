"""cocotbext-axi write memory for a master without AXI IDs (event_ring_writer, audit/13 §2.7).

AxiRamWrite needs awid and bid and breaks on an optional signal the DUT does not have (the bus holds it as None).
The ring needs no IDs: its bursts all answer in order. So the IDs are tied here, in Python, and the ports stay as
the plan lists them.
"""

from cocotb.types import LogicArray
from cocotbext.axi import AxiWriteBus


class TiedSignal:
    """A bus signal that the DUT does not have: one bit, reads 0, writes go nowhere."""

    def __init__(self) -> None:
        self.value = LogicArray("0")

    def __len__(self) -> int:
        return 1

    def setimmediatevalue(self, value) -> None:
        self.value = value


def write_bus(dut, prefix: str) -> AxiWriteBus:
    bus = AxiWriteBus.from_prefix(dut, prefix)
    for channel in (bus.aw, bus.w, bus.b):
        for name, signal in list(channel._signals.items()):
            if signal is not None:
                continue
            if name in ("awid", "bid"):
                channel._signals[name] = TiedSignal()
                setattr(channel, name, channel._signals[name])
            else:
                del channel._signals[name]
                delattr(channel, name)
    return bus
