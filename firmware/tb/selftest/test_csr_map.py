"""Simulator-free checks of the CSR map (firmware/rdl/hft_csr.rdl, audit/10 §2.2, §2.8) and of the
generated C header.

The expected addresses below are written by hand, not computed from the RDL: an unintended shift
of a register fails here, in the C++ header check (csr_header_check.cpp, also hand-written) and in
just gen-check.
"""

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from model.csr import OKAY, SLVERR, CsrModel, csr_map, csr_mems, k5_violations, load_map, mem, regs_by_path

FW = Path(__file__).resolve().parents[2]
REPO = FW.parent
HFT_PKG = FW / "rtl" / "core" / "hft_pkg.sv"
HEADER_CHECK = Path(__file__).with_name("csr_header_check.cpp")

DROPS = ("DISABLED", "FCS", "MAC", "ETHERTYPE", "IP_HDR", "FRAG", "PROTO", "FILTER", "LEN")
MOLD_STATUSES = (
    "OK", "HEARTBEAT", "EOS", "DUP", "DISABLED", "OTHER_FEED", "ABORT", "RUNT", "SESSION", "UNSUPPORTED", "MALFORMED",
)  # fmt: skip
# FEED counters after the per-status ones, with their addresses.
MOLD_EXTRA = {"MSGS": 0x0298, "DUP_MSGS": 0x02A0, "OVERSIZE_MSGS": 0x02A8, "GAP_EVENTS": 0x02B0, "GAP_MSGS": 0x02B8}
ITCH_CLASSES = ("ORDER", "TRADE", "ADMIN", "AUCTION", "UNKNOWN", "MALFORMED")
# ITCH counters after the per-class ones, with their addresses.
ITCH_EXTRA = {"OUT": 0x0670, "FILTERED": 0x0678, "MASKED": 0x0680}

# RISK_REJECT_<reason> in risk_reason_e order (codes 1..10).
REASONS = ("KILL", "TX_OFF", "INVALID", "LOCATE", "QTY", "NOTIONAL", "BUDGET", "RATE", "TX_BUSY", "RING_FULL")
# External registers (hft_csr_wrap answers them through the order command channel, decision 33).
EXTERNAL = {
    "RISK.RISK_COMMIT", "RISK.BUDGET_ADD", "STRATEGY.RULE_COMMIT", "STRATEGY.RULE_ARM", "STRATEGY.RULE_DISARM",
    "TX.TX_COMMIT", "TX.TX_USERREF_NEXT", "TX.TX_USERREF",
}  # fmt: skip

# Every register of the map (F5 + F3 FEED block + F4 ITCH block + F6 RING block + F7 RISK, STRATEGY, TX):
# path -> address.
EXPECTED = {
    "GLOBAL.ID": 0x0000,
    "GLOBAL.VERSION": 0x0004,
    "GLOBAL.SCRATCH": 0x0008,
    "NET.NET_CTRL": 0x0100,
    "NET.LOCAL_MAC_LO": 0x0104,
    "NET.LOCAL_MAC_HI": 0x0108,
    "NET.NET_COMMIT": 0x010C,
    "NET.NET_STATUS": 0x0110,
    **{f"NET.FILTER[{i}].IP": 0x0120 + 8 * i for i in range(8)},
    **{f"NET.FILTER[{i}].PORT_EN": 0x0124 + 8 * i for i in range(8)},
    "FEED.FEED_CTRL": 0x0200,
    "FEED.SESSION_0": 0x0204,
    "FEED.SESSION_1": 0x0208,
    "FEED.SESSION_2": 0x020C,
    "FEED.RESYNC_SEQ_LO": 0x0210,
    "FEED.RESYNC_SEQ_HI": 0x0214,
    "FEED.FEED_COMMIT": 0x0218,
    "FEED.FEED_STATUS": 0x021C,
    "FEED.EXPECTED_SEQ.LO": 0x0220,
    "FEED.EXPECTED_SEQ.HI": 0x0224,
    "FEED.FEED_STATE": 0x0228,
    "FEED.MOLD_OK.LO": 0x0240,
    "FEED.MOLD_OK.HI": 0x0244,
    "FEED.MOLD_HEARTBEAT.LO": 0x0248,
    "FEED.MOLD_HEARTBEAT.HI": 0x024C,
    "FEED.MOLD_EOS.LO": 0x0250,
    "FEED.MOLD_EOS.HI": 0x0254,
    "FEED.MOLD_DUP.LO": 0x0258,
    "FEED.MOLD_DUP.HI": 0x025C,
    "FEED.MOLD_DISABLED.LO": 0x0260,
    "FEED.MOLD_DISABLED.HI": 0x0264,
    "FEED.MOLD_OTHER_FEED.LO": 0x0268,
    "FEED.MOLD_OTHER_FEED.HI": 0x026C,
    "FEED.MOLD_ABORT.LO": 0x0270,
    "FEED.MOLD_ABORT.HI": 0x0274,
    "FEED.MOLD_RUNT.LO": 0x0278,
    "FEED.MOLD_RUNT.HI": 0x027C,
    "FEED.MOLD_SESSION.LO": 0x0280,
    "FEED.MOLD_SESSION.HI": 0x0284,
    "FEED.MOLD_UNSUPPORTED.LO": 0x0288,
    "FEED.MOLD_UNSUPPORTED.HI": 0x028C,
    "FEED.MOLD_MALFORMED.LO": 0x0290,
    "FEED.MOLD_MALFORMED.HI": 0x0294,
    "FEED.MOLD_MSGS.LO": 0x0298,
    "FEED.MOLD_MSGS.HI": 0x029C,
    "FEED.MOLD_DUP_MSGS.LO": 0x02A0,
    "FEED.MOLD_DUP_MSGS.HI": 0x02A4,
    "FEED.MOLD_OVERSIZE_MSGS.LO": 0x02A8,
    "FEED.MOLD_OVERSIZE_MSGS.HI": 0x02AC,
    "FEED.MOLD_GAP_EVENTS.LO": 0x02B0,
    "FEED.MOLD_GAP_EVENTS.HI": 0x02B4,
    "FEED.MOLD_GAP_MSGS.LO": 0x02B8,
    "FEED.MOLD_GAP_MSGS.HI": 0x02BC,
    "RING.RING_CTRL": 0x0800,
    "RING.RING_BASE_LO": 0x0804,
    "RING.RING_BASE_HI": 0x0808,
    "RING.RING_SIZE_LOG2": 0x080C,
    "RING.CONS_IDX": 0x0810,
    "RING.PROD_IDX": 0x0814,
    "RING.RING_STATUS": 0x0818,
    "RING.RING_WRITTEN.LO": 0x0820,
    "RING.RING_WRITTEN.HI": 0x0824,
    "RING.RING_BRESP_ERR.LO": 0x0828,
    "RING.RING_BRESP_ERR.HI": 0x082C,
    "RING.RING_COMMIT_ENTRIES.LO": 0x0840,
    "RING.RING_COMMIT_ENTRIES.HI": 0x0844,
    "RING.RING_DROP_PKTS.LO": 0x0848,
    "RING.RING_DROP_PKTS.HI": 0x084C,
    "RING.RING_DROP_ENTRIES.LO": 0x0850,
    "RING.RING_DROP_ENTRIES.HI": 0x0854,
    "RING.RING_DROP_ORDERS.LO": 0x0858,
    "RING.RING_DROP_ORDERS.HI": 0x085C,
    "RING.RING_ORDER_QUEUED": 0x0860,
    "STATS.STATS_CTRL": 0x0400,
    "STATS.STATS_SEQ": 0x0404,
    "STATS.RX_PKTS.LO": 0x0410,
    "STATS.RX_PKTS.HI": 0x0414,
    "STATS.RX_BYTES.LO": 0x0418,
    "STATS.RX_BYTES.HI": 0x041C,
    "STATS.DROP_DISABLED.LO": 0x0420,
    "STATS.DROP_DISABLED.HI": 0x0424,
    "STATS.DROP_FCS.LO": 0x0428,
    "STATS.DROP_FCS.HI": 0x042C,
    "STATS.DROP_MAC.LO": 0x0430,
    "STATS.DROP_MAC.HI": 0x0434,
    "STATS.DROP_ETHERTYPE.LO": 0x0438,
    "STATS.DROP_ETHERTYPE.HI": 0x043C,
    "STATS.DROP_IP_HDR.LO": 0x0440,
    "STATS.DROP_IP_HDR.HI": 0x0444,
    "STATS.DROP_FRAG.LO": 0x0448,
    "STATS.DROP_FRAG.HI": 0x044C,
    "STATS.DROP_PROTO.LO": 0x0450,
    "STATS.DROP_PROTO.HI": 0x0454,
    "STATS.DROP_FILTER.LO": 0x0458,
    "STATS.DROP_FILTER.HI": 0x045C,
    "STATS.DROP_LEN.LO": 0x0460,
    "STATS.DROP_LEN.HI": 0x0464,
    "ITCH.ITCH_CTRL": 0x0600,
    "ITCH.ITCH_ORDER.LO": 0x0640,
    "ITCH.ITCH_ORDER.HI": 0x0644,
    "ITCH.ITCH_TRADE.LO": 0x0648,
    "ITCH.ITCH_TRADE.HI": 0x064C,
    "ITCH.ITCH_ADMIN.LO": 0x0650,
    "ITCH.ITCH_ADMIN.HI": 0x0654,
    "ITCH.ITCH_AUCTION.LO": 0x0658,
    "ITCH.ITCH_AUCTION.HI": 0x065C,
    "ITCH.ITCH_UNKNOWN.LO": 0x0660,
    "ITCH.ITCH_UNKNOWN.HI": 0x0664,
    "ITCH.ITCH_MALFORMED.LO": 0x0668,
    "ITCH.ITCH_MALFORMED.HI": 0x066C,
    "ITCH.ITCH_OUT.LO": 0x0670,
    "ITCH.ITCH_OUT.HI": 0x0674,
    "ITCH.ITCH_FILTERED.LO": 0x0678,
    "ITCH.ITCH_FILTERED.HI": 0x067C,
    "ITCH.ITCH_MASKED.LO": 0x0680,
    "ITCH.ITCH_MASKED.HI": 0x0684,
    "RISK.MAX_QTY": 0x0A00,
    "RISK.MAX_NOTIONAL_LO": 0x0A04,
    "RISK.MAX_NOTIONAL_HI": 0x0A08,
    "RISK.RATE_REFILL": 0x0A0C,
    "RISK.RATE_PERIOD": 0x0A10,
    "RISK.RATE_BURST": 0x0A14,
    "RISK.RISK_COMMIT": 0x0A18,
    "RISK.RISK_CTRL": 0x0A1C,
    "RISK.BUDGET_ADD": 0x0A20,
    "RISK.BUDGET": 0x0A24,
    "RISK.ORDERS_SENT_HW.LO": 0x0A40,
    "RISK.ORDERS_SENT_HW.HI": 0x0A44,
    "RISK.ORDERS_SENT_HOST.LO": 0x0A48,
    "RISK.ORDERS_SENT_HOST.HI": 0x0A4C,
    "RISK.RISK_REJECT_KILL.LO": 0x0A60,
    "RISK.RISK_REJECT_KILL.HI": 0x0A64,
    "RISK.RISK_REJECT_TX_OFF.LO": 0x0A68,
    "RISK.RISK_REJECT_TX_OFF.HI": 0x0A6C,
    "RISK.RISK_REJECT_INVALID.LO": 0x0A70,
    "RISK.RISK_REJECT_INVALID.HI": 0x0A74,
    "RISK.RISK_REJECT_LOCATE.LO": 0x0A78,
    "RISK.RISK_REJECT_LOCATE.HI": 0x0A7C,
    "RISK.RISK_REJECT_QTY.LO": 0x0A80,
    "RISK.RISK_REJECT_QTY.HI": 0x0A84,
    "RISK.RISK_REJECT_NOTIONAL.LO": 0x0A88,
    "RISK.RISK_REJECT_NOTIONAL.HI": 0x0A8C,
    "RISK.RISK_REJECT_BUDGET.LO": 0x0A90,
    "RISK.RISK_REJECT_BUDGET.HI": 0x0A94,
    "RISK.RISK_REJECT_RATE.LO": 0x0A98,
    "RISK.RISK_REJECT_RATE.HI": 0x0A9C,
    "RISK.RISK_REJECT_TX_BUSY.LO": 0x0AA0,
    "RISK.RISK_REJECT_TX_BUSY.HI": 0x0AA4,
    "RISK.RISK_REJECT_RING_FULL.LO": 0x0AA8,
    "RISK.RISK_REJECT_RING_FULL.HI": 0x0AAC,
    "STRATEGY.RULE_SEL": 0x1000,
    "STRATEGY.RULE_CTRL": 0x1004,
    "STRATEGY.RULE_LOCATE": 0x1008,
    "STRATEGY.RULE_THRESHOLD": 0x100C,
    "STRATEGY.RULE_MIN_SHARES": 0x1010,
    "STRATEGY.RULE_QTY": 0x1014,
    "STRATEGY.RULE_OFFSET": 0x1018,
    "STRATEGY.RULE_COMMIT": 0x101C,
    "STRATEGY.RULE_ARM": 0x1020,
    "STRATEGY.RULE_DISARM": 0x1024,
    "STRATEGY.RULE_STATUS": 0x1028,
    "STRATEGY.RULE_ARMED": 0x102C,
    **{f"STRATEGY.RULE_FIRED_{n}.LO": 0x1040 + 8 * n for n in range(8)},
    **{f"STRATEGY.RULE_FIRED_{n}.HI": 0x1044 + 8 * n for n in range(8)},
    "TX.TX_CTRL": 0x2000,
    "TX.TX_OUCH": 0x2004,
    "TX.TX_COMMIT": 0x2008,
    "TX.TX_STATUS": 0x200C,
    "TX.TX_USERREF_NEXT": 0x2010,
    "TX.TX_USERREF": 0x2014,
    **{f"TX.TX_TEMPLATE[{i}]": 0x2020 + 4 * i for i in range(10)},
    "TX.TX_TEMPLATE_TAIL": 0x2048,
    "TX.MBOX_SIDE": 0x2050,
    "TX.MBOX_QTY": 0x2054,
    "TX.MBOX_PRICE": 0x2058,
    "TX.MBOX_LOCATE": 0x205C,
    "TX.MBOX_STOCK_0": 0x2060,
    "TX.MBOX_STOCK_1": 0x2064,
    "TX.MBOX_TIF": 0x2068,
    **{f"TX.MBOX_CLORDID[{i}]": 0x2070 + 4 * i for i in range(3)},
    "TX.MBOX_CLORDID_TAIL": 0x207C,
    "TX.MBOX_SEND": 0x2080,
    "TX.MBOX_SPACE": 0x2084,
    "TX.MBOX_STATUS": 0x2088,
    "TX.MBOX_DROP.LO": 0x2090,
    "TX.MBOX_DROP.HI": 0x2094,
}
# Every memory of the map: path -> (address, entries, width).
EXPECTED_MEMS = {"SYMBOL": (0x8000, 2048, 32), "LOCATE_ALLOW": (0xA000, 2048, 32)}

MAX_READ_WINDOW_REGS = 43  # registers in one 512-byte read window (see test_read_windows_stay_small)

# Blocks with registers and their address ranges; the rest of the 64 KiB window is reserved except the SYMBOL
# memory at 0x8000 and LOCATE_ALLOW at 0xA000.
BLOCKS = {"GLOBAL": (0x0000, 0x0100), "NET": (0x0100, 0x0200), "FEED": (0x0200, 0x0300), "RING": (0x0800, 0x0900),
          "STATS": (0x0400, 0x0500), "ITCH": (0x0600, 0x0700), "RISK": (0x0A00, 0x0B00), "STRATEGY": (0x1000, 0x1100),
          "TX": (0x2000, 0x2100)}  # fmt: skip


def test_every_register_at_its_address():
    got = {r.path: r.addr for r in csr_map()}
    assert got == EXPECTED


def test_read_windows_stay_small():
    """The generated read mux (--rt-read-fanin) has one register stage per 512-byte window, and the logic in front of
    it grows with the registers in that window. RING at 0x0300, sharing FEED's window with 60 registers, failed the A7
    125 MHz gate by 0.234 ns; alone at 0x0800 it passes (audit/13 S8). A new block goes in a window with room."""
    windows = Counter(r.addr >> 9 for r in csr_map())
    assert max(windows.values()) <= MAX_READ_WINDOW_REGS, dict(windows)


def test_constants():
    regs = regs_by_path()
    assert regs["GLOBAL.ID"].fields[0].reset == 0x48465431  # "HFT1"
    assert regs["GLOBAL.VERSION"].fields[0].reset == 0x0005_0004  # + F3 FEED, F4 ITCH, SYMBOL, F6 RING, F7 (minor)


def test_drop_counters_follow_reason_codes():
    """DROP_<reason> exists for every RX_DROP_* of hft_pkg.sv, at 0x0420 + 8 * (code - 1)."""
    codes = {m[1]: int(m[2]) for m in re.finditer(r"\bRX_DROP_(\w+)\s*=\s*4'd(\d+)", HFT_PKG.read_text())}
    assert set(codes) == set(DROPS), "reason codes and drop counters differ"
    regs = regs_by_path()
    for name, code in codes.items():
        assert regs[f"STATS.DROP_{name}.LO"].addr == 0x0420 + 8 * (code - 1), name
    assert not [p for p in regs if p.startswith("STATS.DROP_") and p.split(".")[1][5:] not in codes]


def test_mold_counters_follow_status_codes():
    """MOLD_<status> exists for every mold_status_e code of hft_pkg.sv, at 0x0240 + 8 * code; the
    message and gap counters follow at the MOLD_CNT_* indices of hft_pkg.sv."""
    pkg = HFT_PKG.read_text()
    codes = {m[1]: int(m[2]) for m in re.finditer(r"\bMOLD_(\w+)\s*=\s*4'd(\d+)", pkg)}
    assert set(codes) == set(MOLD_STATUSES), "status codes and MOLD_* counters differ"
    regs = regs_by_path()
    for name, code in codes.items():
        assert regs[f"FEED.MOLD_{name}.LO"].addr == 0x0240 + 8 * code, name
    # MOLD_CNT_MSGS = 1 + int'(MOLD_MALFORMED), then +1 .. +4 (the counter index in mold_stats)
    first = 1 + codes["MALFORMED"]
    offsets = {m[1]: int(m[2]) for m in re.finditer(r"\bMOLD_CNT_(\w+)\s*=\s*MOLD_CNT_MSGS\s*\+\s*(\d+)", pkg)}
    index = {"MSGS": first} | {n: first + k for n, k in offsets.items()}
    assert set(index) == set(MOLD_EXTRA)
    for name, i in index.items():
        assert regs[f"FEED.MOLD_{name}.LO"].addr == 0x0240 + 8 * i == MOLD_EXTRA[name], name
    mold = [p for p in regs if p.startswith("FEED.MOLD_") and p.endswith(".LO")]
    assert len(mold) == len(MOLD_STATUSES) + len(MOLD_EXTRA)


def test_itch_counters_follow_class_codes():
    """ITCH_<class> exists for every itch_class_e code of hft_pkg.sv, at 0x0640 + 8 * code; the
    outcome counters follow at ITCH_N_CLASS + itch_outcome_e code (the index in itch_stats)."""
    pkg = HFT_PKG.read_text()
    classes = {m[1]: int(m[2]) for m in re.finditer(r"\bITCH_(\w+)\s*=\s*3'd(\d+)", pkg)}
    assert set(classes) == set(ITCH_CLASSES), "class codes and ITCH_* counters differ"
    outcomes = {m[1]: int(m[2]) for m in re.finditer(r"\bITCH_RES_(\w+)\s*=\s*2'd(\d+)", pkg)}
    assert set(outcomes) == set(ITCH_EXTRA), "outcome codes and ITCH_* counters differ"
    regs = regs_by_path()
    for name, code in classes.items():
        assert regs[f"ITCH.ITCH_{name}.LO"].addr == 0x0640 + 8 * code, name
    for name, code in outcomes.items():
        assert regs[f"ITCH.ITCH_{name}.LO"].addr == 0x0640 + 8 * (len(classes) + code) == ITCH_EXTRA[name], name
    itch = [p for p in regs if p.startswith("ITCH.ITCH_") and p.endswith(".LO")]
    assert len(itch) == len(ITCH_CLASSES) + len(ITCH_EXTRA)


def test_risk_reject_counters_follow_reason_codes():
    """RISK_REJECT_<reason> exists for every risk_reason_e code but NONE, at 0x0A60 + 8 * (code - 1), and the
    order_stats indices of hft_pkg.sv put them in that order (ORD_CNT_REJ + reason - 1)."""
    pkg = HFT_PKG.read_text()
    codes = {m[1]: int(m[2]) for m in re.finditer(r"\bRISK_(\w+)\s*=\s*4'd(\d+)", pkg)}
    assert codes.pop("NONE") == 0
    assert codes == {name: i + 1 for i, name in enumerate(REASONS)}, "reason codes and RISK_REJECT_* differ"
    regs = regs_by_path()
    for name, code in codes.items():
        assert regs[f"RISK.RISK_REJECT_{name}.LO"].addr == 0x0A60 + 8 * (code - 1), name
    assert len([p for p in regs if p.startswith("RISK.RISK_REJECT_") and p.endswith(".LO")]) == len(REASONS)
    assert "ORD_N_REASONS = int'(RISK_RING_FULL)" in pkg, "order_stats counts every reason"


def test_external_registers():
    """The command registers are external (hft_csr_wrap answers them after the command is in effect), each
    either write-only with hw = r (the hardware takes the value) or TX_USERREF read-only; no other register is
    external. The model keeps nothing for them: a write is OK and recorded, a read gives the hardware value."""
    regs = csr_map()
    ext = {r.path for r in regs if any(f.external for f in r.fields)}
    assert ext == EXTERNAL
    for r in regs:
        if r.path in ext:
            assert all(f.external and not f.storage for f in r.fields), r.path
    by = regs_by_path()
    assert all(by[p].writable and not by[p].readable for p in EXTERNAL - {"TX.TX_USERREF"})
    assert by["TX.TX_USERREF"].readable and not by["TX.TX_USERREF"].writable
    m = CsrModel()
    assert m.write(by["TX.TX_USERREF_NEXT"].addr, 0x1234_5678, strb=0b0011) == (OKAY, set())
    assert m.ext_writes == [("TX.TX_USERREF_NEXT", 0x5678)]
    assert m.read(by["TX.TX_USERREF_NEXT"].addr) == (0, SLVERR), "write-only"
    m.hw["TX.TX_USERREF.value"] = 9
    assert m.read(by["TX.TX_USERREF"].addr) == (9, OKAY)


def test_symbol_memory():
    """SYMBOL: 2048 x 32 bit, read/write, aligned to its size, no register inside it; the model
    answers it like a RAM with byte strobes (0 at reset)."""
    got = {m.path: (m.addr, m.entries, m.width) for m in csr_mems()}
    assert got == EXPECTED_MEMS
    sym = mem("SYMBOL")
    assert sym.size == 0x2000 and sym.addr % sym.size == 0 and sym.sw_read and sym.sw_write and sym.desc
    assert not [r for r in csr_map() if sym.word(r.addr) is not None]
    m = CsrModel()
    assert m.read(0x8000) == (0, OKAY) and m.read(0x9FFC) == (0, OKAY)
    assert m.write(0x8004, 0xAABB_CCDD, strb=0b0101) == (OKAY, set())
    assert m.read(0x8004) == (0x00BB_00DD, OKAY)
    assert m.read(0x8006) == (0x00BB_00DD, OKAY), "unaligned: the word"
    assert m.read(0xC000) == (0, SLVERR), "past LOCATE_ALLOW, the last memory"


def test_locate_allow_memory():
    """LOCATE_ALLOW: 2048 x 32 bit, read/write, right after SYMBOL, aligned to its size, no register inside it;
    the model answers it like SYMBOL, as its own RAM."""
    loc = mem("LOCATE_ALLOW")
    assert loc.size == 0x2000 and loc.addr == 0xA000 and loc.sw_read and loc.sw_write and loc.desc
    assert not [r for r in csr_map() if loc.word(r.addr) is not None]
    m = CsrModel()
    assert m.write(0xA008, 0x1122_3344, strb=0b1010) == (OKAY, set())
    assert m.read(0xA008) == (0x1100_3300, OKAY)
    assert m.read(0x8008) == (0, OKAY), "SYMBOL is another memory"
    assert m.read(0xBFFC) == (0, OKAY)


def _router_windows() -> list[tuple[int, int]]:
    """(base, mask) of hft_csr_regs' router windows, read from hft_pkg (CSR_ORDER_WIN_*, window 0 in the low bits)."""
    text = HFT_PKG.read_text(encoding="utf-8")

    def words(name: str) -> list[int]:
        m = re.search(rf"localparam logic \[[^\]]+\] {name} = \{{([^}}]+)\}};", text)
        assert m, f"{name} not found in hft_pkg.sv"
        return [int(w.strip().split("'h")[1], 16) for w in m[1].split(",")][::-1]

    n = int(re.search(r"localparam int unsigned CSR_ORDER_N_WIN = (\d+);", text)[1])
    base, mask = words("CSR_ORDER_WIN_BASE"), words("CSR_ORDER_WIN_MASK")
    assert len(base) == len(mask) == n
    return list(zip(base, mask, strict=True))


def test_router_windows_hold_exactly_the_order_blocks():
    """Decision 41: the router sends an access to hft_order_csr when its address is in a window. Every RISK,
    STRATEGY and TX register is in one, and no other register or memory word is (a block placed there later
    would answer SLVERR from hft_order_csr). hft_order_csr has exactly those registers at the same addresses."""
    from systemrdl import RDLCompiler
    from systemrdl.node import RegNode

    from model.csr import RDL

    wins = _router_windows()
    assert wins == [(0x0A00, 0xFE00), (0x1000, 0xF000), (0x2000, 0xF000)], "hand-written: RISK, STRATEGY, TX"

    def in_win(a: int) -> bool:
        return any(a & m == b for b, m in wins)

    order = {"RISK", "STRATEGY", "TX"}
    wrong = [(r.path, hex(r.addr)) for r in csr_map() if in_win(r.addr) != (r.path.split(".")[0] in order)]
    assert not wrong, f"registers on the wrong side of the router windows: {wrong}"
    for m in csr_mems():
        assert not [a for a in range(m.addr, m.addr + m.size, 4) if in_win(a)], f"{m.path} overlaps a window"
    rdlc = RDLCompiler()
    rdlc.compile_file(str(RDL))
    top = rdlc.elaborate(top_def_name="hft_order_csr").top
    got = sorted((n.get_path().split(".", 1)[1], n.absolute_address) for n in top.descendants(unroll=True)
                 if isinstance(n, RegNode))  # fmt: skip
    want = sorted((r.path, r.addr) for r in csr_map() if r.path.split(".")[0] in order)
    assert got == want, "hft_order_csr differs from hft_csr's RISK, STRATEGY and TX"


def test_layout_rules():
    """32-bit registers, 4-byte aligned, no overlap, only inside the F5 blocks, a desc on every field."""
    regs = csr_map()
    assert all(r.width == 32 and r.addr % 4 == 0 for r in regs)
    assert len({r.addr for r in regs}) == len(regs)
    for r in regs:
        lo, hi = BLOCKS[r.block]
        assert lo <= r.addr < hi, f"{r.path} at {r.addr:#06x} outside {r.block}"
        for f in r.fields:
            assert f.desc, f"{r.path}.{f.name} has no desc"


def test_access_kinds():
    """sw access of each register as the CSR contract states it (audit/10 §2.2-2.4)."""
    regs = regs_by_path()

    def kind(path: str) -> str:
        r = regs[path]
        return {(True, True): "RW", (True, False): "RO", (False, True): "WO"}[r.readable, r.writable]

    ro = {"GLOBAL.ID", "GLOBAL.VERSION", "NET.NET_STATUS", "STATS.STATS_SEQ", "FEED.FEED_STATUS", "FEED.FEED_STATE"}
    ro |= {p for p in EXPECTED if p.startswith(("STATS.", "FEED.", "ITCH.", "RING.")) and p.endswith((".LO", ".HI"))}
    ro |= {"RING.PROD_IDX", "RING.RING_STATUS"}
    ro -= {"FEED.RESYNC_SEQ_LO", "FEED.RESYNC_SEQ_HI"}  # software writes the resync value
    ro |= {p for p in EXPECTED if p.startswith(("RISK.", "STRATEGY.", "TX.")) and p.endswith((".LO", ".HI"))}
    ro |= {"RING.RING_ORDER_QUEUED", "RISK.BUDGET", "STRATEGY.RULE_STATUS", "STRATEGY.RULE_ARMED", "TX.TX_STATUS",
           "TX.TX_USERREF", "TX.MBOX_SPACE", "TX.MBOX_STATUS"}  # fmt: skip
    wo = {"NET.NET_COMMIT", "STATS.STATS_CTRL", "FEED.FEED_COMMIT", "TX.MBOX_SEND"}
    wo |= EXTERNAL - {"TX.TX_USERREF"}
    assert {p: kind(p) for p in EXPECTED} == {p: "RO" if p in ro else "WO" if p in wo else "RW" for p in EXPECTED}
    pulses = {f"{r.path}.{f.name}" for r in csr_map() for f in r.fields if f.singlepulse}
    assert pulses == {"NET.NET_COMMIT.commit", "STATS.STATS_CTRL.snapshot", "FEED.FEED_COMMIT.commit",
                      "FEED.FEED_COMMIT.resync", "TX.MBOX_SEND.send"}  # fmt: skip
    # Reset contract (decision 5): kill 1, tx_en 0; RULE_SEL and the shadows 0.
    assert {f.name: (f.lsb, f.width, f.reset) for f in regs["RISK.RISK_CTRL"].fields} == {"kill": (0, 1, 1)}
    assert {f.name: (f.lsb, f.width, f.reset) for f in regs["TX.TX_CTRL"].fields} == {"tx_en": (0, 1, 0)}
    rule = {f.name: (f.lsb, f.width) for f in regs["STRATEGY.RULE_CTRL"].fields}
    assert rule == {"enable": (0, 1), "msg_mask": (1, 3), "side_match": (4, 2), "price_ge": (6, 1),
                    "action_side": (8, 2), "qty_min": (10, 1), "price_threshold_mode": (11, 1), "tif": (16, 8)}  # fmt: skip
    ouch = {f.name: (f.lsb, f.width) for f in regs["TX.TX_OUCH"].fields}
    assert ouch == {"display": (0, 8), "capacity": (8, 8), "ise": (16, 8), "cross_type": (24, 8)}
    assert {f.name: f.lsb for f in regs["TX.TX_STATUS"].fields} == {"cfg_valid": 0, "cfg_refused": 1,
                                                                   "userref_ignored": 2}  # fmt: skip
    assert regs["TX.TX_TEMPLATE_TAIL"].fields[0].lsb == 16 and regs["TX.MBOX_CLORDID_TAIL"].fields[0].lsb == 16
    assert regs["NET.NET_CTRL"].fields[0].reset == 0, "rx_en must reset to 0 (receiver off)"
    ctrl = {f.name: f for f in regs["FEED.FEED_CTRL"].fields}
    assert (ctrl["enable"].lsb, ctrl["enable"].reset) == (0, 0), "feed enable must reset to 0"
    assert (ctrl["session_check"].lsb, ctrl["resync_learn"].lsb, ctrl["filter_mask"].lsb) == (1, 2, 8)
    assert {f.name: f.lsb for f in regs["FEED.FEED_COMMIT"].fields} == {"commit": 0, "resync": 1}
    assert {f.name: f.lsb for f in regs["FEED.FEED_STATE"].fields} == {"synced": 0, "eos": 1}
    assert regs["FEED.SESSION_2"].fields[0].lsb == 16, "session bytes 8..9 in [31:16]"
    itch = {f.name: (f.lsb, f.width, f.reset) for f in regs["ITCH.ITCH_CTRL"].fields}
    assert itch == {"class_en": (0, 6, 0), "class_filter": (8, 6, 0)}, "class_en must reset to 0 (nothing output)"
    assert {f.name: (f.lsb, f.width, f.reset) for f in regs["RING.RING_CTRL"].fields} == {"enable": (0, 1, 0)}
    assert {f.name: (f.lsb, f.width, f.reset) for f in regs["RING.RING_BASE_LO"].fields} == {"base": (12, 20, 0)}
    assert {f.name: (f.lsb, f.width, f.reset) for f in regs["RING.RING_SIZE_LOG2"].fields} == {"size_log2": (0, 5, 0)}
    assert {f.name: f.lsb for f in regs["RING.RING_STATUS"].fields} == {"bresp_err": 0, "enable_err": 1}


def test_no_field_written_by_both_sw_and_hw():
    """K5 rule: software and hardware never write the same field."""
    assert k5_violations(csr_map()) == []


def test_k5_rule_rejects_a_shared_field(tmp_path):
    """The K5 check bites: a field with sw=rw and hw=w (or a sw-writable counter) is reported."""
    rdl = tmp_path / "bad.rdl"
    rdl.write_text("""
        addrmap bad {
            reg { field { sw = rw; hw = w; } a[0:0]; field { sw = rw; hw = r; } ok[1:1]; } R0 @ 0x0;
            reg { field { sw = rw; hw = na; counter; } c[7:0]; } R1 @ 0x4;
        };
    """)
    assert k5_violations(load_map(rdl)) == ["R0.a", "R1.c"]


def test_model_access_rules():
    """The register model answers like the block should (the reference for the S3 bus tests)."""
    m = CsrModel()
    a = {p: r.addr for p, r in regs_by_path().items()}
    assert m.read(a["GLOBAL.ID"]) == (0x48465431, OKAY)
    assert m.write(a["GLOBAL.ID"], 0) == (SLVERR, set())
    assert m.read(a["NET.NET_COMMIT"]) == (0, SLVERR)
    assert m.read(0x0500) == (0, SLVERR) and m.write(0xFFFC, 1) == (SLVERR, set())
    assert m.write(a["NET.FILTER[2].PORT_EN"], 0xFFFF_FFFF, strb=0b0100) == (OKAY, set())
    assert m.read(a["NET.FILTER[2].PORT_EN"]) == (1 << 16, OKAY), "only byte 2 written, only en is in it"
    assert m.write(a["NET.NET_COMMIT"], 1) == (OKAY, {"NET.NET_COMMIT.commit"})
    assert m.write(a["NET.NET_COMMIT"], 1, strb=0b1110) == (OKAY, set()), "byte 0 not enabled: no pulse"
    with pytest.raises(KeyError):
        m.read(a["STATS.STATS_SEQ"])
    m.hw["STATS.STATS_SEQ.seq"] = 7
    assert m.read(a["STATS.STATS_SEQ"]) == (7, OKAY)


def test_c_header_compiles_with_hand_written_offsets():
    """software/include/gen/hft_csr.h compiles as C++20, and its struct offsets equal the
    hand-written addresses in csr_header_check.cpp (static_assert)."""
    gxx = shutil.which("g++")
    assert gxx, "g++ not found (run: just setup)"
    cmd = [gxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", f"-I{REPO / 'software' / 'include'}"]
    done = subprocess.run([*cmd, str(HEADER_CHECK)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_header_check_covers_the_anchors():
    """csr_header_check.cpp names every register of EXPECTED, so a new register cannot skip it."""
    text = HEADER_CHECK.read_text()
    for path, address in EXPECTED.items():
        assert f"OFS({path}, 0x{address:04X})" in text, f"csr_header_check.cpp lacks {path}"
    for path, (address, entries, width) in EXPECTED_MEMS.items():
        last = address + (entries - 1) * width // 8
        for anchor in (f"OFS({path}.mem, 0x{address:04X})", f"OFS({path}.mem[{entries - 1}], 0x{last:04X})"):
            assert anchor in text, f"csr_header_check.cpp lacks {anchor}"
