"""common/cosim_vectors.py: the co-simulation vectors pass the models' own checks (audit/15 §2.7). No simulator."""

from model.desc_gen import CTRL_BYTES, FIELDS, VERSION, Kind, decode
from model.csr import addr
from model.order import Source, decode_ouch, ip_checksum_ok, unwrap_soupbin
from common import cosim_vectors as cv
from common.exchange import Exchange

COMMANDS = {"memory": 2, "csr_w": 2, "csr_r": 3, "poll": 3, "tx_ready": 1, "rx": (1, 2), "idle": 1, "snapshot": 0,
            "drain": 2, "expect_ring": None, "expect_tx": 1, "end": 0}  # fmt: skip


def parsed() -> list[tuple[str, list[str]]]:
    return [(ln.split()[0], ln.split()[1:]) for ln in cv.scenario().lines]


def test_every_line_is_a_known_command():
    for cmd, args in parsed():
        assert cmd in COMMANDS, cmd
        n = COMMANDS[cmd]
        if isinstance(n, int):
            assert len(args) == n, (cmd, args)
        elif isinstance(n, tuple):
            assert len(args) in n, (cmd, args)
    assert parsed()[-1] == ("end", [])
    assert cv.scenario().lines == cv.scenario().lines, "the scenario is deterministic"


def test_ring_entries_decode_and_masks_are_the_timestamps():
    entries = [args for cmd, args in parsed() if cmd == "expect_ring"]
    drain = next(args for cmd, args in parsed() if cmd == "drain")
    assert int(drain[0], 16) == len(entries) > 0
    kinds = []
    for idx, (hexed, *masks) in enumerate(entries):
        e = bytes.fromhex(hexed)
        d = decode(e)
        kinds.append(Kind(d["kind"]))
        assert d["ring_idx"] == idx
        assert all(e[b] == 0x80 | VERSION for b in CTRL_BYTES), "first pass: phase 1"
        for m in masks:
            name, place = m.split("@")
            ofs, size = (int(x) for x in place.split(":"))
            assert name in cv.TS_FIELDS and FIELDS[Kind(d["kind"])][name][:2] == (ofs, size), m
            assert not any(e[ofs : ofs + size]), f"masked {name} is not 0"
        if Kind(d["kind"]) in (Kind.ITCH, Kind.ITCH_RAW):
            assert [m.split("@")[0] for m in masks] == ["sof_ts"]
        if Kind(d["kind"]) == Kind.ORDER_SENT:
            want = ["tx_ts"] if d["source"] == Source.HOST else ["sof_ts", "tx_ts"]
            assert [m.split("@")[0] for m in masks] == want, d
    assert kinds.count(Kind.ORDER_SENT) == 2 and Kind.ITCH_RAW in kinds and Kind.ITCH in kinds


def test_tx_frames_pass_the_exchange_model():
    frames = [bytes.fromhex(args[0]) for cmd, args in parsed() if cmd == "expect_tx"]
    drain = next(args for cmd, args in parsed() if cmd == "drain")
    assert int(drain[1], 16) == len(frames) == 2
    ex = Exchange(cv.TXC.template, cv.USERREF)
    got = [ex.receive(f) for f in frames]
    assert all(ip_checksum_ok(f[14:34]) for f in frames)
    assert [o.user_ref for o in got] == [1, 2]
    assert [decode_ouch(unwrap_soupbin(f[42:])) for f in frames] == got
    assert got[1].clordid == cv.MBOX.clordid, "the host order keeps its ClOrdID"


def test_counters_follow_the_scenario():
    """The counters the models give, against hand counts of the scenario: 4 frames (one bad FCS), A and D accepted,
    C a duplicate, B aborted; ITCH: A's two messages and D's first out, D's second filtered."""
    want = {"STATS.RX_PKTS": 4, "STATS.DROP_FCS": 1, "FEED.MOLD_OK": 2, "FEED.MOLD_DUP": 1, "FEED.MOLD_ABORT": 1,
            "ITCH.ITCH_OUT": 3, "ITCH.ITCH_FILTERED": 1, "RISK.ORDERS_SENT_HW": 1, "RISK.ORDERS_SENT_HOST": 1}  # fmt: skip
    reads = {int(args[0], 16): int(args[1], 16) for cmd, args in parsed() if cmd == "csr_r"}
    for name, n in want.items():
        assert reads[addr(f"{name}.LO")] == n, name
    entries = sum(cmd == "expect_ring" for cmd, _ in parsed())
    assert reads[addr("RING.RING_COMMIT_ENTRIES.LO")] == entries == reads[addr("RING.PROD_IDX")]
    rx = [args for cmd, args in parsed() if cmd == "rx"]
    assert sum(args[1:] == ["bad_fcs"] for args in rx) == 1
