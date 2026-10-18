"""Register model of the CSR map, read from firmware/rdl/hft_csr.rdl (audit/10 §2.8). No simulator.

Decision: the model compiles the RDL with systemrdl-compiler (the front end PeakRDL uses) and
never reads the generated SystemVerilog, so a generator bug cannot be mirrored here. It knows
each register's address and sw access, each field's reset value and kind, and answers an AXI4-Lite
access like the block should: rdata and RRESP/BRESP, with SLVERR for unmapped addresses, writes
to read-only and reads from write-only registers (err_if_bad_addr/err_if_bad_rw), and no side
effect for a rejected access.

Values the hardware drives (hw=w fields, counters) are not known to the model: the test sets
them in CsrModel.hw before it checks a read.
"""

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from systemrdl import RDLCompiler
from systemrdl.node import FieldNode, RegNode

RDL = Path(__file__).resolve().parents[2] / "rdl" / "hft_csr.rdl"
OKAY, SLVERR = 0, 2  # AXI4-Lite xRESP


@dataclass(frozen=True)
class Field:
    name: str
    lsb: int
    width: int
    sw_read: bool
    sw_write: bool
    hw_write: bool  # hardware changes the value: hw=w/rw, counter, hwset/hwclr
    hw_read: bool  # the value is a hwif output
    singlepulse: bool
    reset: int | None  # None: no reset (a hardware-driven wire)
    desc: str | None

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb

    @property
    def storage(self) -> bool:
        """Software-written state the model keeps (singlepulse fields read nothing back)."""
        return self.sw_write and not self.singlepulse


@dataclass(frozen=True)
class Reg:
    path: str  # e.g. "NET.FILTER[3].IP" (the top addrmap name is dropped)
    addr: int
    width: int
    fields: tuple[Field, ...]

    @property
    def readable(self) -> bool:
        return any(f.sw_read for f in self.fields)

    @property
    def writable(self) -> bool:
        return any(f.sw_write for f in self.fields)

    @property
    def block(self) -> str:
        return self.path.split(".")[0]


def _field(node: FieldNode) -> Field:
    hw_write = node.is_hw_writable or any(node.get_property(p) for p in ("counter", "hwset", "hwclr"))
    reset = node.get_property("reset")
    return Field(
        name=node.inst_name,
        lsb=node.lsb,
        width=node.width,
        sw_read=node.is_sw_readable,
        sw_write=node.is_sw_writable,
        hw_write=hw_write,
        hw_read=node.is_hw_readable,
        singlepulse=bool(node.get_property("singlepulse")),
        reset=reset if isinstance(reset, int) else None,
        desc=node.get_property("desc"),
    )


def load_map(rdl: Path = RDL) -> tuple[Reg, ...]:
    """Every register of the RDL file, in address order (arrays unrolled)."""
    rdlc = RDLCompiler()
    rdlc.compile_file(str(rdl))
    top = rdlc.elaborate().top
    regs = [
        Reg(
            path=node.get_path().split(".", 1)[1],
            addr=node.absolute_address,
            width=node.get_property("regwidth"),
            fields=tuple(_field(f) for f in node.fields()),
        )
        for node in top.descendants(unroll=True)
        if isinstance(node, RegNode)
    ]
    return tuple(sorted(regs, key=lambda r: r.addr))


@cache
def csr_map() -> tuple[Reg, ...]:
    return load_map()


def regs_by_path() -> dict[str, Reg]:
    return {r.path: r for r in csr_map()}


def addr(path: str) -> int:
    return regs_by_path()[path].addr


def k5_violations(regs: tuple[Reg, ...]) -> list[str]:
    """Fields that both software and hardware write (legacy K5: a hardware update overwrote a host
    write that still answered OKAY). The map keeps the two writers in different registers."""
    return [f"{r.path}.{f.name}" for r in regs for f in r.fields if f.sw_write and f.hw_write]


def wstrb_mask(strb: int) -> int:
    return sum(0xFF << (8 * i) for i in range(4) if strb >> i & 1)


@dataclass
class CsrModel:
    """Expected bus behaviour of the block. `hw` holds the value of hardware-driven fields by
    "<reg path>.<field name>"; a read of such a field without a value raises (the test must say
    what it expects)."""

    regs: tuple[Reg, ...] = field(default_factory=csr_map)
    hw: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_addr = {r.addr: r for r in self.regs}
        self.state = {
            (r.path, f.name): f.reset or 0 for r in self.regs for f in r.fields if f.storage
        }  # fmt: skip

    def reset(self) -> None:
        self.__post_init__()

    def write(self, address: int, data: int, strb: int = 0xF) -> tuple[int, set[str]]:
        """BRESP and the singlepulse fields that fire ("<reg path>.<field name>")."""
        reg = self.by_addr.get(address & ~3)
        if reg is None or not reg.writable:
            return SLVERR, set()
        bits = wstrb_mask(strb)
        pulses = set()
        for f in reg.fields:
            if not f.sw_write:
                continue
            m = f.mask & bits
            if f.singlepulse:
                if data & m:
                    pulses.add(f"{reg.path}.{f.name}")
                continue
            old = self.state[reg.path, f.name] << f.lsb
            self.state[reg.path, f.name] = ((old & ~m) | (data & m)) >> f.lsb
        return OKAY, pulses

    def read(self, address: int) -> tuple[int, int]:
        """(RDATA, RRESP). A rejected read returns 0 as data."""
        reg = self.by_addr.get(address & ~3)
        if reg is None or not reg.readable:
            return 0, SLVERR
        value = 0
        for f in reg.fields:
            if not f.sw_read:
                continue
            if f.storage:
                v = self.state[reg.path, f.name]
            elif f.hw_write:
                key = f"{reg.path}.{f.name}"
                if key not in self.hw:
                    raise KeyError(f"model: no value for hardware-driven field {key}")
                v = self.hw[key]
            else:
                v = f.reset or 0  # constant
            value |= (v << f.lsb) & f.mask
        return value, OKAY

    def shadow(self, path: str, name: str) -> int:
        """Current software value of a storage field."""
        return self.state[path, name]
