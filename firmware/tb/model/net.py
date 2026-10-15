"""Golden model of eth_ip_udp_rx (audit/09 §2.2, §2.5, §2.11). Pure Python, no simulator.

Decision: expect() parses the frame with scapy and applies the receive rules as one
if-chain in the priority order of 09 §2.2. It never uses the RTL's beat/lane layout,
so a byte-order bug in the RTL (legacy K2) cannot be mirrored here.

Generation: udp_frame() builds a valid frame with scapy, every length and checksum set
explicitly (no scapy auto-fill or padding). Faults are injected on the raw bytes
(with_field, with_ip_options, with_vlan, pad_to, slicing). random_traffic() mixes
every outcome category round-robin, so coverage does not depend on the seed.
"""

import ipaddress
import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether
from scapy.packet import Raw
from scapy.utils import checksum

ETH_MIN_FRAME = 60  # bytes on the AXIS bus (the MAC strips the FCS)
UDP_PAYLOAD_OFS = 14 + 20 + 8  # Ethernet II + IPv4 without options + UDP
STAT_BYTES_MAX = 0xFFFF  # stat_bytes saturates
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"


class RxReason(IntEnum):
    """Receive outcome = rx_reason_e in hft_pkg.sv (checked by test_reason_codes_match_rtl)."""

    OK = 0
    DISABLED = 1
    FCS = 2
    MAC = 3
    ETHERTYPE = 4
    IP_HDR = 5
    FRAG = 6
    PROTO = 7
    FILTER = 8
    LEN = 9


@dataclass(frozen=True)
class RxConfig:
    rx_en: bool = True
    local_mac: str = "02:00:00:00:00:01"
    accept_bcast: bool = False
    filters: tuple[tuple[str, int, bool], ...] = ()  # (ip, port, enable), index = filter_idx


@dataclass(frozen=True)
class RxMeta:
    src_ip: int
    dst_ip: int
    dst_port: int
    length: int  # payload bytes (UDP length - 8)
    filter_idx: int


@dataclass(frozen=True)
class RxExpect:
    """reason, nbytes: the stat event. payload: the bytes on m_axis_udp (None: no output
    packet). aborted: tuser on its last beat. meta: m_udp_* while that packet is out."""

    reason: RxReason
    nbytes: int
    payload: bytes | None
    aborted: bool
    meta: RxMeta | None


@dataclass(frozen=True)
class _Header:
    reason: RxReason | None  # None: every header check passed
    ip_len: int = 0
    udp_len: int = 0
    meta: RxMeta | None = None


def ip_int(ip: str) -> int:
    return int(ipaddress.IPv4Address(ip))


def _mac_str(mac: int) -> str:
    return mac.to_bytes(6, "big").hex(":")


def mcast_mac(ip: str) -> str:
    """Ethernet address of an IPv4 multicast group (RFC 1112 §6.4: 01:00:5e + low 23 bits)."""
    return _mac_str(0x01005E000000 | (ip_int(ip) & 0x7FFFFF))


def mac_accepted(dst_mac: str, dst_ip: str, cfg: RxConfig) -> bool:
    """09 §2.5 step 1: own unicast address, broadcast if enabled, or the multicast address of dst_ip."""
    mac = dst_mac.lower()
    if mac == cfg.local_mac.lower():
        return True
    if cfg.accept_bcast and mac == BROADCAST_MAC:
        return True
    return ipaddress.IPv4Address(dst_ip).is_multicast and mac == mcast_mac(dst_ip)


def _header_verdict(frame: bytes, cfg: RxConfig) -> _Header:
    if len(frame) < UDP_PAYLOAD_OFS:
        return _Header(RxReason.LEN)  # headers incomplete: no check on missing bytes
    eth = Ether(frame[:14])
    # The IPv4 header is read even when the EtherType is not IPv4: the multicast MAC rule
    # (checked first) compares with the destination address field.
    ip = IP(frame[14:34])
    udp = UDP(frame[34:42])
    if not mac_accepted(eth.dst, ip.dst, cfg):
        return _Header(RxReason.MAC)
    if eth.type != 0x0800:
        return _Header(RxReason.ETHERTYPE)
    if ip.version != 4 or ip.ihl != 5 or checksum(frame[14:34]) != 0:
        return _Header(RxReason.IP_HDR)
    if ip.flags.MF or ip.frag != 0:
        return _Header(RxReason.FRAG)
    if ip.proto != 17:
        return _Header(RxReason.PROTO)
    dst = ip_int(ip.dst)
    hits = [i for i, (fip, fport, en) in enumerate(cfg.filters) if en and ip_int(fip) == dst and fport == udp.dport]
    if not hits:
        return _Header(RxReason.FILTER)
    if udp.len != ip.len - 20 or udp.len <= 8:
        return _Header(RxReason.LEN)
    meta = RxMeta(ip_int(ip.src), dst, udp.dport, udp.len - 8, hits[0])
    return _Header(None, ip.len, udp.len, meta)


def _frame_length_error(n: int, ip_len: int) -> bool:
    """Runt, truncated datagram, or bytes after the datagram that are not Ethernet padding."""
    end = 14 + ip_len
    if n < ETH_MIN_FRAME or n < end:
        return True
    return n > end and n != ETH_MIN_FRAME


def expect(frame: bytes, tuser: bool, cfg: RxConfig) -> RxExpect:
    """Reference outcome of one frame (09 §2.2), independent of the RTL's byte offsets.

    A frame whose headers pass delivers frame[42 : 42 + udp_len - 8]: padding never comes
    out, a truncated frame delivers what it has. Zero bytes means no output packet.
    """
    n = min(len(frame), STAT_BYTES_MAX)
    if not cfg.rx_en:
        return RxExpect(RxReason.DISABLED, n, None, False, None)
    hdr = _header_verdict(frame, cfg)
    if hdr.reason is not None:
        return RxExpect(RxReason.FCS if tuser else hdr.reason, n, None, False, None)
    payload = frame[UDP_PAYLOAD_OFS : UDP_PAYLOAD_OFS + hdr.udp_len - 8] or None
    meta = hdr.meta if payload else None
    if tuser:
        return RxExpect(RxReason.FCS, n, payload, payload is not None, meta)
    if _frame_length_error(len(frame), hdr.ip_len):
        return RxExpect(RxReason.LEN, n, payload, payload is not None, meta)
    return RxExpect(RxReason.OK, n, payload, False, meta)


# ---------------------------------------------------------------------------------------
# Frame generation (test stimulus). The byte offsets below are the wire layout of
# Ethernet II + IPv4 without options, used only to inject faults; expect() does not use them.

_FIELDS = {  # name: (offset, size)
    "dst_mac": (0, 6),
    "src_mac": (6, 6),
    "ethertype": (12, 2),
    "ver_ihl": (14, 1),
    "ip_len": (16, 2),
    "ident": (18, 2),
    "flags_frag": (20, 2),
    "ttl": (22, 1),
    "proto": (23, 1),
    "ip_csum": (24, 2),
    "src_ip": (26, 4),
    "dst_ip": (30, 4),
    "sport": (34, 2),
    "dport": (36, 2),
    "udp_len": (38, 2),
}


def udp_frame(
    payload: bytes,
    *,
    dst_mac: str = "02:00:00:00:00:01",
    src_mac: str = "02:00:00:00:00:99",
    src_ip: str = "10.0.0.1",
    dst_ip: str = "10.0.0.2",
    sport: int = 1234,
    dport: int = 5000,
    ident: int = 0,
    ttl: int = 64,
) -> bytes:
    """A valid Ethernet/IPv4/UDP frame (DF set, no UDP checksum), without padding."""
    pkt = (
        Ether(dst=dst_mac, src=src_mac, type=0x0800)
        / IP(version=4, ihl=5, tos=0, len=28 + len(payload), id=ident, flags="DF", frag=0, ttl=ttl, proto=17,
             chksum=0, src=src_ip, dst=dst_ip)  # fmt: skip
        / UDP(sport=sport, dport=dport, len=8 + len(payload), chksum=0)
        / Raw(payload)
    )
    return fix_ip_checksum(bytes(pkt))


def fix_ip_checksum(frame: bytes) -> bytes:
    """Recompute the IPv4 header checksum over the IHL-sized header (at least 20 bytes)."""
    end = 14 + max(20, (frame[14] & 0x0F) * 4)
    hdr = frame[14:24] + b"\0\0" + frame[26:end]
    return frame[:24] + checksum(hdr).to_bytes(2, "big") + frame[26:]


def with_field(frame: bytes, name: str, value: int | str | bytes, *, fix_csum: bool = True) -> bytes:
    """Overwrite one header field. Strings are MAC ("aa:bb:..") or IPv4 ("a.b.c.d") addresses.
    The IPv4 checksum is recomputed unless fix_csum=False or the field is the checksum."""
    ofs, size = _FIELDS[name]
    if isinstance(value, str):
        raw = bytes.fromhex(value.replace(":", "")) if ":" in value else ipaddress.IPv4Address(value).packed
    elif isinstance(value, int):
        raw = value.to_bytes(size, "big")
    else:
        raw = value
    assert len(raw) == size, f"{name}: {len(raw)} bytes, field has {size}"
    out = frame[:ofs] + raw + frame[ofs + size :]
    return fix_ip_checksum(out) if fix_csum and name != "ip_csum" else out


def with_ip_options(frame: bytes, options: bytes = b"\x01\x01\x01\x00") -> bytes:
    """Insert IPv4 options (default NOP NOP NOP EOL); IHL, total length and checksum follow."""
    assert options and len(options) % 4 == 0
    ip_len = int.from_bytes(frame[16:18], "big") + len(options)
    out = frame[:34] + options + frame[34:]
    out = with_field(out, "ver_ihl", 0x40 | (5 + len(options) // 4), fix_csum=False)
    return with_field(out, "ip_len", ip_len)


def with_vlan(frame: bytes, vid: int = 100) -> bytes:
    """Insert an 802.1Q tag after the source MAC (EtherType 0x8100)."""
    return frame[:12] + b"\x81\x00" + (vid & 0xFFF).to_bytes(2, "big") + frame[12:]


def pad_to(frame: bytes, n: int = ETH_MIN_FRAME) -> bytes:
    """Zero padding up to n bytes (a MAC pads short frames to 60)."""
    return frame + bytes(max(0, n - len(frame)))


def last_beat_payload_bytes(payload_len: int) -> int:
    """Payload bytes in the last 8-byte input beat that holds payload (1..8): the tail case."""
    return (UDP_PAYLOAD_OFS + payload_len - 1) % 8 + 1


# ---------------------------------------------------------------------------------------
# Random traffic

Make = Callable[[], tuple[bytes, bool, bool]]  # -> (frame, tuser, rx_en)


@dataclass(frozen=True)
class Traffic:
    frame: bytes
    tuser: bool  # FCS error flag on the last beat
    rx_en: bool  # cfg_rx_en at this frame's SOF
    category: str
    intent: RxReason  # what the generator meant to produce (expect() must agree)


def random_config(rng: random.Random, n_filters: int = 8, accept_bcast: bool = True) -> RxConfig:
    """Every entry enabled with a distinct (ip, port): even indices unicast, odd multicast."""
    ips = [
        f"10.{rng.randrange(256)}.{rng.randrange(256)}.{i + 1}" if i % 2 == 0
        else f"{rng.randint(224, 239)}.{rng.randrange(256)}.{rng.randrange(256)}.{i + 1}"
        for i in range(n_filters)
    ]  # fmt: skip
    ports = rng.sample(range(1024, 65536), n_filters)
    mac = "02:" + ":".join(f"{rng.randrange(256):02x}" for _ in range(5))
    return RxConfig(True, mac, accept_bcast, tuple((ip, port, True) for ip, port in zip(ips, ports)))


class _Gen:
    """Frame factory for one config: accepted frames, and single faults on top of them."""

    def __init__(self, rng: random.Random, cfg: RxConfig) -> None:
        self.rng, self.cfg = rng, cfg
        self.enabled = [i for i, (_, _, en) in enumerate(cfg.filters) if en]
        self.mcast = [i for i in self.enabled if ipaddress.IPv4Address(cfg.filters[i][0]).is_multicast]
        self.disabled = [i for i, (_, _, en) in enumerate(cfg.filters) if not en]
        self.pairs = {(ip_int(cfg.filters[i][0]), cfg.filters[i][1]) for i in self.enabled}
        if not self.mcast or len(self.pairs) != len(self.enabled):
            raise ValueError("config needs a multicast entry and a distinct (ip, port) per enabled entry")

    def payload_len(self, tail: int | None = None, padded: bool = False, jumbo: bool = False) -> int:
        """Random payload length; padded: the frame is shorter than 60 B before padding."""
        if padded:
            lo, hi = 1, 17
        elif jumbo:
            lo, hi = 1473, 8972  # beyond a 1514-B frame, up to a 9000-B MTU
        else:
            lo, hi = 18, 1472 if self.rng.random() < 0.1 else 300
        while True:
            n = self.rng.randint(lo, hi)
            if tail is None or last_beat_payload_bytes(n) == tail:
                return n

    def random_mac(self) -> str:
        """A unicast address that is not ours."""
        while True:
            mac = "02:" + ":".join(f"{self.rng.randrange(256):02x}" for _ in range(5))
            if mac != self.cfg.local_mac.lower():
                return mac

    def accepted(self, idx: int | None = None, mac: str = "auto", length: int | None = None) -> bytes:
        """A frame for filter entry idx; mac = ucast | mcast | bcast | auto (mcast for group IPs)."""
        rng = self.rng
        idx = rng.choice(self.mcast if mac == "mcast" else self.enabled) if idx is None else idx
        ip, port, _ = self.cfg.filters[idx]
        if mac == "bcast":
            dst = BROADCAST_MAC
        elif mac == "ucast" or not ipaddress.IPv4Address(ip).is_multicast:
            dst = self.cfg.local_mac
        else:
            dst = mcast_mac(ip)
        n = self.payload_len() if length is None else length
        frame = udp_frame(
            rng.randbytes(n),
            dst_mac=dst,
            src_mac=self.random_mac(),
            src_ip=f"10.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randint(1, 254)}",
            dst_ip=ip,
            sport=rng.randrange(1, 65536),
            dport=port,
            ident=rng.randrange(65536),
            ttl=rng.randint(1, 255),
        )
        return pad_to(frame)

    def bad_checksum(self) -> bytes:
        """Flip one bit in a header field that no earlier check reads (ident, TTL, csum, src IP)."""
        frame = self.accepted()
        pos = self.rng.choice([18, 19, 22, 24, 25, 26, 27, 28, 29])
        return frame[:pos] + bytes([frame[pos] ^ (1 << self.rng.randrange(8))]) + frame[pos + 1 :]

    def unmatched_port(self, frame: bytes) -> int:
        """A port that no enabled entry pairs with the frame's dst IP."""
        ip = int.from_bytes(frame[30:34], "big")
        while True:
            port = self.rng.randrange(1, 65536)
            if (ip, port) not in self.pairs:
                return port

    def unmatched_ip(self, frame: bytes) -> str:
        """A unicast address that no enabled entry pairs with the frame's dst port."""
        port = int.from_bytes(frame[36:38], "big")
        while True:
            ip = f"10.{self.rng.randrange(256)}.{self.rng.randrange(256)}.{self.rng.randint(1, 254)}"
            if (ip_int(ip), port) not in self.pairs:
                return ip

    def disabled_entry(self) -> bytes:
        """A frame for a disabled entry whose (ip, port) no enabled entry has."""
        cfg = self.cfg
        free = [i for i in self.disabled if (ip_int(cfg.filters[i][0]), cfg.filters[i][1]) not in self.pairs]
        if not free:
            raise ValueError("every disabled entry duplicates an enabled one")
        return self.accepted(idx=self.rng.choice(free))

    def runt(self) -> bytes:
        """Complete datagram in a frame cut below 60 B (no padding)."""
        n = self.payload_len(padded=True)
        return self.accepted(length=n)[: self.rng.randint(UDP_PAYLOAD_OFS + n, ETH_MIN_FRAME - 1)]

    def truncated(self) -> bytes:
        """The frame ends inside the datagram, after the headers."""
        frame = self.accepted(length=self.rng.randint(30, 300))
        return frame[: self.rng.randint(UDP_PAYLOAD_OFS, len(frame) - 1)]

    def other_udp_len(self, frame: bytes) -> int:
        right = int.from_bytes(frame[16:18], "big") - 20
        return self.rng.choice([right - 1, right + 1, right + self.rng.randint(2, 100), 0])

    def other_group_mac(self, frame: bytes) -> str:
        """Multicast MAC of another group than the frame's dst IP (low 23 bits changed)."""
        return _mac_str(int.from_bytes(frame[:6], "big") ^ self.rng.randint(1, 0x7FFFFF))

    def bit23_mac(self, frame: bytes) -> str:
        """01:00:5e with bit 23 set: never an IPv4 multicast address."""
        return _mac_str(int.from_bytes(frame[:6], "big") | 1 << 23)

    def field(self, name: str, value: Callable[[bytes], int | str], mac: str = "auto") -> Make:
        """An accepted frame with one field replaced by value(frame); a valid frame otherwise."""

        def make() -> tuple[bytes, bool, bool]:
            frame = self.accepted(mac=mac)
            return with_field(frame, name, value(frame)), False, True

        return make

    def frame(self, build: Callable[[], bytes], tuser: bool = False, rx_en: bool = True) -> Make:
        return lambda: (build(), tuser, rx_en)

    def categories(self) -> list[tuple[str, int, RxReason, Make]]:
        """(name, weight, intended reason, make)."""
        rng, R, f = self.rng, RxReason, self.frame
        cats = [
            ("ok_ucast", 2, R.OK, f(lambda: self.accepted(mac="ucast"))),
            ("ok_mcast", 2, R.OK, f(lambda: self.accepted(mac="mcast"))),
            ("ok_jumbo", 1, R.OK, f(lambda: self.accepted(length=self.payload_len(jumbo=True)))),
            ("disabled", 1, R.DISABLED, f(self.accepted, rx_en=False)),
            ("fcs_rejected", 1, R.FCS, f(lambda: with_field(self.accepted(), "dst_mac", self.random_mac()), tuser=True)),
            ("len_short", 1, R.LEN, f(lambda: self.accepted()[: rng.randint(1, UDP_PAYLOAD_OFS - 1)])),
            ("len_runt", 1, R.LEN, f(self.runt)),
            ("len_truncated", 1, R.LEN, f(self.truncated)),
            ("len_trailing", 1, R.LEN, f(lambda: self.accepted() + rng.randbytes(rng.randint(1, 8)))),
            ("len_udp_mismatch", 1, R.LEN, self.field("udp_len", self.other_udp_len)),
            ("len_udp_empty", 1, R.LEN, f(lambda: self.accepted(length=0))),
            ("mac_ucast", 1, R.MAC, self.field("dst_mac", lambda _: self.random_mac(), "ucast")),
            ("mac_mcast_group", 1, R.MAC, self.field("dst_mac", self.other_group_mac, "mcast")),
            ("mac_mcast_bit23", 1, R.MAC, self.field("dst_mac", self.bit23_mac, "mcast")),
            ("ethertype_arp", 1, R.ETHERTYPE, self.field("ethertype", lambda _: 0x0806)),
            ("ethertype_ipv6", 1, R.ETHERTYPE, self.field("ethertype", lambda _: 0x86DD)),
            ("ethertype_vlan", 1, R.ETHERTYPE, f(lambda: with_vlan(self.accepted(mac="ucast"), rng.randrange(4096)))),
            ("ip_version", 1, R.IP_HDR, self.field("ver_ihl", lambda _: 0x65)),
            ("ip_ihl_options", 1, R.IP_HDR, f(lambda: with_ip_options(self.accepted()))),
            ("ip_ihl4", 1, R.IP_HDR, self.field("ver_ihl", lambda _: 0x44)),
            ("ip_csum", 1, R.IP_HDR, f(self.bad_checksum)),
            ("frag_mf", 1, R.FRAG, self.field("flags_frag", lambda _: 0x2000 | rng.randrange(0x2000))),
            ("frag_offset", 1, R.FRAG, self.field("flags_frag", lambda _: rng.choice([0, 0x4000]) | rng.randint(1, 0x1FFF))),
            ("proto_tcp", 1, R.PROTO, self.field("proto", lambda _: 6)),
            ("proto_icmp", 1, R.PROTO, self.field("proto", lambda _: 1)),
            ("proto_igmp", 1, R.PROTO, self.field("proto", lambda _: 2)),
            ("filter_port", 1, R.FILTER, self.field("dport", self.unmatched_port)),
            ("filter_ip", 1, R.FILTER, self.field("dst_ip", self.unmatched_ip, "ucast")),
        ]  # fmt: skip
        if self.cfg.accept_bcast:
            cats.append(("ok_bcast", 2, R.OK, f(lambda: self.accepted(mac="bcast"))))
        else:
            cats.append(("mac_bcast_off", 1, R.MAC, f(lambda: self.accepted(mac="bcast"))))
        if self.disabled:
            cats.append(("filter_disabled_entry", 1, R.FILTER, f(self.disabled_entry)))
        for i in self.enabled:
            cats.append((f"ok_filter_{i}", 1, R.OK, f(lambda i=i: self.accepted(idx=i))))
        for t in range(1, 9):
            tail = lambda t=t: self.accepted(length=self.payload_len(tail=t))  # noqa: E731
            cats += [
                (f"ok_tail_{t}", 1, R.OK, f(tail)),
                (f"ok_tail_{t}_padded", 1, R.OK, f(lambda t=t: self.accepted(length=self.payload_len(t, padded=True)))),
                (f"fcs_tail_{t}", 1, R.FCS, f(tail, tuser=True)),
            ]  # fmt: skip
        return cats


def random_traffic(rng: random.Random, n: int, cfg: RxConfig) -> list[Traffic]:
    """n frames. Each round holds every category `weight` times in random order, so every
    category (and so every outcome) appears once n covers one round, whatever the seed."""
    cats = _Gen(rng, cfg).categories()
    schedule = [c for c in cats for _ in range(c[1])]
    out: list[Traffic] = []
    while len(out) < n:
        rnd = schedule[:]
        rng.shuffle(rnd)
        for name, _, intent, make in rnd[: n - len(out)]:
            frame, tuser, rx_en = make()
            out.append(Traffic(frame, tuser, rx_en, name, intent))
    return out
