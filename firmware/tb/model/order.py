"""Golden model of the order message and the TX port (docs/protocol/order_msg.md; audit/14 §2.6,
decisions 9-13). Pure Python, no simulator.

Decision: the OUCH 5.0 Enter Order layout is written out here from the official specification
(revision 1.05, section 2.1) as a struct format, and the SoupBinTCP 3.00 Unsequenced Data envelope
from its section 2.3.2. Nothing here uses the RTL's beat layout.

Rules:
- Frame = the host's 42-byte Ethernet/IPv4/UDP template, unchanged, + SoupBinTCP 'U' envelope
  (Packet Length 48, big-endian) + the 47-byte Enter Order fixed part (Appendage Length 0): 92 bytes.
- Prices inside the core are ITCH Price(4) (u32); OUCH Price is the same scale in 8 bytes.
- UserRefNum: one counter per TX port, taken by each sent order, then +1; it stops at 0xFFFFFFFF.
  The host writes the next value after reset (decision 12).
- Hardware ClOrdID: "HW" + rule digit + UserRefNum as 8 upper-case hex digits + 3 spaces (P3).
- valid_order(): what the exchange would accept and read as a limit order (decision 9, 10, 13).
"""

import struct
from dataclasses import dataclass, field
from enum import IntEnum

from model import net

TEMPLATE_LEN = 42  # Ethernet II + IPv4 without options + UDP
SOUP_LEN = 3  # Packet Length (2) + Packet Type (1)
OUCH_LEN = 47  # Enter Order fixed part, Appendage Length included
FRAME_LEN = TEMPLATE_LEN + SOUP_LEN + OUCH_LEN  # 92
SOUP_PACKET_LENGTH = 1 + OUCH_LEN  # "length of the rest of the packet": type byte + message

PRICE_LIMIT_MAX = 0x7735939C  # $199,999.9900, the largest limit price
QTY_LIMIT = 1_000_000  # Quantity must be greater than 0 and less than this
USER_REF_MAX = 0xFFFF_FFFF
SIDES = b"BS"  # decision 10: no sell short (T, E)
TIFS = b"035E"  # Day, IOC, GTX, After hours; not GTT (6), which needs the ExpireTime appendage
CLORDID_LEN = 14
CLORDID_CHARS = frozenset(b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ")
TX_QUEUE_DEPTH = 8  # P7

# Type, UserRefNum, Side, Quantity, Symbol, Price, TIF, Display, Capacity, ISE, CrossType, ClOrdID,
# Appendage Length (OUCH 5.0 section 2.1, offsets 0..45).
_OUCH = struct.Struct(">cIcI8sQccccc14sH")
assert _OUCH.size == OUCH_LEN


class Source(IntEnum):
    """ORDER_SENT and RISK_REJECT `source`."""

    RULE = 1
    HOST = 2


@dataclass(frozen=True)
class TxConfig:
    """TX shadow: the header template and the OUCH constants of every order."""

    template: bytes
    display: int = ord("Y")
    capacity: int = ord("P")
    ise: int = ord("N")
    cross_type: int = ord("N")


@dataclass(frozen=True)
class OrderReq:
    """An order on its way to risk_check (audit/14 §2.2). price is a Python int: a rule's
    threshold + offset may leave 0..2**32-1, which risk rejects (INVALID). clordid is the host's
    14 bytes, or None for a hardware order (built when it is sent)."""

    source: Source
    side: int
    qty: int
    price: int
    locate: int
    stock: bytes
    tif: int
    rule: int = 0
    clordid: bytes | None = None
    seq: int = 0  # hardware: the triggering message's
    sof_ts: int = 0  # hardware: its packet's
    epoch: int = 0  # hardware: its packet's cfg_epoch; host: the one in force at the risk decision


@dataclass(frozen=True)
class Ouch:
    """The Enter Order fields as sent (Type 'O' and Appendage Length 0 are implied)."""

    user_ref: int
    side: int
    qty: int
    stock: bytes
    price: int
    tif: int
    display: int
    capacity: int
    ise: int
    cross_type: int
    clordid: bytes


def encode_ouch(o: Ouch) -> bytes:
    return _OUCH.pack(
        b"O", o.user_ref, bytes([o.side]), o.qty, o.stock, o.price, bytes([o.tif]), bytes([o.display]),
        bytes([o.capacity]), bytes([o.ise]), bytes([o.cross_type]), o.clordid, 0,
    )  # fmt: skip


class OrderError(ValueError):
    """A byte string that is not one Enter Order message as the core sends it."""


def decode_ouch(msg: bytes) -> Ouch:
    if len(msg) != OUCH_LEN:
        raise OrderError(f"Enter Order is {OUCH_LEN} bytes, got {len(msg)}")
    typ, ref, side, qty, stock, price, tif, disp, cap, ise, cross, clordid, app_len = _OUCH.unpack(msg)
    if typ != b"O":
        raise OrderError(f"message type {typ!r}, want b'O'")
    if app_len != 0:
        raise OrderError(f"Appendage Length {app_len}, want 0")
    return Ouch(ref, side[0], qty, stock, price, tif[0], disp[0], cap[0], ise[0], cross[0], clordid)


def soupbin_u(msg: bytes) -> bytes:
    return (1 + len(msg)).to_bytes(2, "big") + b"U" + msg


def unwrap_soupbin(pkt: bytes) -> bytes:
    if len(pkt) < SOUP_LEN or int.from_bytes(pkt[:2], "big") != len(pkt) - 2:
        raise OrderError(f"SoupBinTCP Packet Length does not match the {len(pkt)}-byte packet")
    if pkt[2:3] != b"U":
        raise OrderError(f"SoupBinTCP packet type {pkt[2:3]!r}, want b'U'")
    return pkt[SOUP_LEN:]


def template(**udp: str | int) -> bytes:
    """A valid header template, as a host writes it (net.udp_frame arguments)."""
    return net.udp_frame(bytes(SOUP_LEN + OUCH_LEN), **udp)[:TEMPLATE_LEN]


def frame(cfg: TxConfig, o: Ouch) -> bytes:
    return cfg.template + soupbin_u(encode_ouch(o))


def hw_clordid(rule: int, user_ref: int) -> bytes:
    assert 0 <= rule <= 9 and 0 <= user_ref <= USER_REF_MAX
    return f"HW{rule}{user_ref:08X}   ".encode()


def ouch_of(req: OrderReq, user_ref: int, cfg: TxConfig) -> Ouch:
    clordid = req.clordid if req.source == Source.HOST else hw_clordid(req.rule, user_ref)
    return Ouch(
        user_ref, req.side, req.qty, req.stock, req.price, req.tif, cfg.display, cfg.capacity, cfg.ise,
        cfg.cross_type, clordid,
    )  # fmt: skip


def valid_price(price: int) -> bool:
    return 1 <= price <= PRICE_LIMIT_MAX


def valid_order(req: OrderReq) -> bool:
    """False: risk_check rejects it with INVALID (decisions 9, 10, 13). UserRefNum exhaustion is
    the TX port's state, not the order's (TxPort.refs_left)."""
    clordid_ok = req.source == Source.RULE or (
        req.clordid is not None and len(req.clordid) == CLORDID_LEN and set(req.clordid) <= CLORDID_CHARS
    )
    return (
        valid_price(req.price) and 0 < req.qty < QTY_LIMIT and req.side in SIDES and req.tif in TIFS and clordid_ok
    )


@dataclass
class Sent:
    req: OrderReq
    user_ref: int
    frame: bytes


@dataclass
class TxPort:
    """The TX queue (P7) and the UserRefNum counter. accept() takes an order that passed risk;
    send() takes the next one when the MAC is ready; tx_off() empties the queue (decision 7)."""

    cfg: TxConfig
    user_ref_next: int = 1
    queue: list[OrderReq] = field(default_factory=list)

    def free(self) -> bool:
        return len(self.queue) < TX_QUEUE_DEPTH

    def refs_left(self) -> int:
        """UserRefNums not yet taken by a sent or queued order."""
        return USER_REF_MAX + 1 - self.user_ref_next - len(self.queue)

    def accept(self, req: OrderReq) -> None:
        assert self.free() and self.refs_left() > 0
        self.queue.append(req)

    def send(self) -> Sent | None:
        if not self.queue:
            return None
        req = self.queue.pop(0)
        ref = self.user_ref_next
        self.user_ref_next += 1
        return Sent(req, ref, frame(self.cfg, ouch_of(req, ref, self.cfg)))

    def tx_off(self) -> list[OrderReq]:
        out, self.queue = self.queue, []
        return out

    def write_user_ref_next(self, value: int) -> None:
        assert 1 <= value <= USER_REF_MAX + 1 and not self.queue
        self.user_ref_next = value
