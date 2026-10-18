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

from model.csr import OKAY, SLVERR, CsrModel, csr_map, k5_violations, load_map, regs_by_path

FW = Path(__file__).resolve().parents[2]
REPO = FW.parent
HFT_PKG = FW / "rtl" / "core" / "hft_pkg.sv"
HEADER_CHECK = Path(__file__).with_name("csr_header_check.cpp")

DROPS = ("DISABLED", "FCS", "MAC", "ETHERTYPE", "IP_HDR", "FRAG", "PROTO", "FILTER", "LEN")

# Every register of the F5 map: path -> address.
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
}

# Blocks with registers in F5 and their address ranges; the rest of the 64 KiB window is
# reserved (FEED 0x0200, RING 0x0300, RISK 0x0500, STRATEGY 0x1000, TX 0x2000, SYMBOL 0x8000).
BLOCKS = {"GLOBAL": (0x0000, 0x0100), "NET": (0x0100, 0x0200), "STATS": (0x0400, 0x0500)}


def test_every_register_at_its_address():
    got = {r.path: r.addr for r in csr_map()}
    assert got == EXPECTED


def test_constants():
    regs = regs_by_path()
    assert regs["GLOBAL.ID"].fields[0].reset == 0x48465431  # "HFT1"
    assert regs["GLOBAL.VERSION"].fields[0].reset == 0x0005_0000  # F5 map


def test_drop_counters_follow_reason_codes():
    """DROP_<reason> exists for every RX_DROP_* of hft_pkg.sv, at 0x0420 + 8 * (code - 1)."""
    codes = {m[1]: int(m[2]) for m in re.finditer(r"\bRX_DROP_(\w+)\s*=\s*4'd(\d+)", HFT_PKG.read_text())}
    assert set(codes) == set(DROPS), "reason codes and drop counters differ"
    regs = regs_by_path()
    for name, code in codes.items():
        assert regs[f"STATS.DROP_{name}.LO"].addr == 0x0420 + 8 * (code - 1), name
    assert not [p for p in regs if p.startswith("STATS.DROP_") and p.split(".")[1][5:] not in codes]


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

    ro = {"GLOBAL.ID", "GLOBAL.VERSION", "NET.NET_STATUS", "STATS.STATS_SEQ"}
    ro |= {p for p in EXPECTED if p.startswith("STATS.") and p.endswith((".LO", ".HI"))}
    wo = {"NET.NET_COMMIT", "STATS.STATS_CTRL"}
    assert {p: kind(p) for p in EXPECTED} == {p: "RO" if p in ro else "WO" if p in wo else "RW" for p in EXPECTED}
    pulses = {f"{r.path}.{f.name}" for r in csr_map() for f in r.fields if f.singlepulse}
    assert pulses == {"NET.NET_COMMIT.commit", "STATS.STATS_CTRL.snapshot"}
    assert regs["NET.NET_CTRL"].fields[0].reset == 0, "rx_en must reset to 0 (receiver off)"


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
    assert m.read(0x0200) == (0, SLVERR) and m.write(0xFFFC, 1) == (SLVERR, set())
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
