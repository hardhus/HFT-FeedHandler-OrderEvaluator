"""Simulator-free checks of the CSR map (firmware/rdl/hft_csr.rdl, audit/10 §2.2, §2.8) and of the
generated C header.

The expected addresses below are written by hand, not computed from the RDL: an unintended shift
of a register fails here, in the C++ header check (csr_header_check.cpp, also hand-written) and in
just gen-check.
"""

import re
import shutil
import subprocess
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

# Every register of the map (F5 + F3 FEED block + F4 ITCH block): path -> address.
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
}
# Every memory of the map: path -> (address, entries, width).
EXPECTED_MEMS = {"SYMBOL": (0x8000, 2048, 32)}

# Blocks with registers and their address ranges; the rest of the 64 KiB window is reserved
# (RING 0x0300, RISK 0x0500, STRATEGY 0x1000, TX 0x2000) except the SYMBOL memory at 0x8000.
BLOCKS = {"GLOBAL": (0x0000, 0x0100), "NET": (0x0100, 0x0200), "FEED": (0x0200, 0x0300), "STATS": (0x0400, 0x0500),
          "ITCH": (0x0600, 0x0700)}  # fmt: skip


def test_every_register_at_its_address():
    got = {r.path: r.addr for r in csr_map()}
    assert got == EXPECTED


def test_constants():
    regs = regs_by_path()
    assert regs["GLOBAL.ID"].fields[0].reset == 0x48465431  # "HFT1"
    assert regs["GLOBAL.VERSION"].fields[0].reset == 0x0005_0002  # + F3 FEED, F4 ITCH and SYMBOL (minor)


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
    assert m.read(0xA000) == (0, SLVERR), "past the end"


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
    ro |= {p for p in EXPECTED if p.startswith(("STATS.", "FEED.", "ITCH.")) and p.endswith((".LO", ".HI"))}
    ro -= {"FEED.RESYNC_SEQ_LO", "FEED.RESYNC_SEQ_HI"}  # software writes the resync value
    wo = {"NET.NET_COMMIT", "STATS.STATS_CTRL", "FEED.FEED_COMMIT"}
    assert {p: kind(p) for p in EXPECTED} == {p: "RO" if p in ro else "WO" if p in wo else "RW" for p in EXPECTED}
    pulses = {f"{r.path}.{f.name}" for r in csr_map() for f in r.fields if f.singlepulse}
    assert pulses == {"NET.NET_COMMIT.commit", "STATS.STATS_CTRL.snapshot", "FEED.FEED_COMMIT.commit",
                      "FEED.FEED_COMMIT.resync"}  # fmt: skip
    assert regs["NET.NET_CTRL"].fields[0].reset == 0, "rx_en must reset to 0 (receiver off)"
    ctrl = {f.name: f for f in regs["FEED.FEED_CTRL"].fields}
    assert (ctrl["enable"].lsb, ctrl["enable"].reset) == (0, 0), "feed enable must reset to 0"
    assert (ctrl["session_check"].lsb, ctrl["resync_learn"].lsb, ctrl["filter_mask"].lsb) == (1, 2, 8)
    assert {f.name: f.lsb for f in regs["FEED.FEED_COMMIT"].fields} == {"commit": 0, "resync": 1}
    assert {f.name: f.lsb for f in regs["FEED.FEED_STATE"].fields} == {"synced": 0, "eos": 1}
    assert regs["FEED.SESSION_2"].fields[0].lsb == 16, "session bytes 8..9 in [31:16]"
    itch = {f.name: (f.lsb, f.width, f.reset) for f in regs["ITCH.ITCH_CTRL"].fields}
    assert itch == {"class_en": (0, 6, 0), "class_filter": (8, 6, 0)}, "class_en must reset to 0 (nothing output)"


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
    assert m.read(0x0300) == (0, SLVERR) and m.write(0xFFFC, 1) == (SLVERR, set())
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
