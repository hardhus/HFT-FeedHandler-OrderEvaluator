"""Simulated exchange: receives the TX frames and checks them as an OUCH 5.0 host on UDP would
(audit/14 §2.9, docs/protocol/order_msg.md).

Decision: the headers are parsed with scapy (not with order.py's byte offsets), and the IPv4
checksum is verified by scapy's own recomputation, so a header bug the model shares cannot hide.
The SoupBinTCP envelope and the Enter Order message go through model.order's decoder.

Checks on every frame: 92 bytes; Ethernet/IPv4/UDP equal to the template the host wrote (so the
host's addresses, lengths and checksum went out unchanged); IPv4 checksum valid, total length 78,
UDP length 58, UDP checksum 0; SoupBinTCP Packet Length 48 and type 'U'; Type 'O', Appendage Length
0; the order would be accepted as a limit order (Side B/S, TIF, Quantity, Price, ClOrdID
characters); UserRefNum exactly the previous + 1 (the core never skips one; the exchange itself
only needs "strictly increasing").
"""

from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from model.order import (
    CLORDID_CHARS,
    FRAME_LEN,
    OUCH_LEN,
    QTY_LIMIT,
    SIDES,
    SOUP_LEN,
    TEMPLATE_LEN,
    TIFS,
    Ouch,
    decode_ouch,
    unwrap_soupbin,
    valid_price,
)


class ExchangeError(AssertionError):
    """A frame the exchange would drop, reject or read differently than the core meant."""


class Exchange:
    def __init__(self, template: bytes, user_ref_next: int = 1) -> None:
        assert len(template) == TEMPLATE_LEN
        self.template = template
        self.user_ref_next = user_ref_next
        self.orders: list[Ouch] = []

    def receive(self, frame: bytes) -> Ouch:
        if len(frame) != FRAME_LEN:
            raise ExchangeError(f"frame is {len(frame)} bytes, want {FRAME_LEN}")
        if frame[:TEMPLATE_LEN] != self.template:
            raise ExchangeError(f"header {frame[:TEMPLATE_LEN].hex()} is not the template {self.template.hex()}")
        eth = Ether(frame)
        ip, udp = eth[IP], eth[UDP]
        sent_csum = ip.chksum
        del ip.chksum
        if IP(bytes(ip)).chksum != sent_csum:
            raise ExchangeError(f"IPv4 checksum {sent_csum:#06x} is wrong")
        ip_len = TEMPLATE_LEN - 14 + SOUP_LEN + OUCH_LEN
        if (ip.len, ip.ihl, ip.flags.value, ip.frag, ip.proto) != (ip_len, 5, 2, 0, 17):
            raise ExchangeError(f"IPv4 header: len {ip.len}, ihl {ip.ihl}, flags {ip.flags}, proto {ip.proto}")
        if (udp.len, udp.chksum) != (8 + SOUP_LEN + OUCH_LEN, 0):
            raise ExchangeError(f"UDP length {udp.len}, checksum {udp.chksum:#06x}")
        try:
            o = decode_ouch(unwrap_soupbin(frame[TEMPLATE_LEN:]))
        except ValueError as e:
            raise ExchangeError(str(e)) from e
        self._check_fields(o)
        if o.user_ref != self.user_ref_next:
            raise ExchangeError(f"UserRefNum {o.user_ref}, want {self.user_ref_next}")
        self.user_ref_next += 1
        self.orders.append(o)
        return o

    @staticmethod
    def _check_fields(o: Ouch) -> None:
        bad = []
        if o.side not in SIDES:
            bad.append(f"side {o.side:#x}")
        if o.tif not in TIFS:
            bad.append(f"tif {o.tif:#x}")
        if not 0 < o.qty < QTY_LIMIT:
            bad.append(f"qty {o.qty}")
        if not valid_price(o.price):
            bad.append(f"price {o.price:#x}")
        if not set(o.clordid) <= CLORDID_CHARS:
            bad.append(f"clordid {o.clordid!r}")
        if bad:
            raise ExchangeError("invalid order: " + ", ".join(bad))
