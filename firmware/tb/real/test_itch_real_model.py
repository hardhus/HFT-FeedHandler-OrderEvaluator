"""The ITCH and MoldUDP64 models on real Nasdaq TotalView-ITCH 5.0 data (audit/12 S11).

Marker `real`: needs the network (common/itch_real.py downloads each manifest source into the
build directory) and is not part of `just test`; run with `just itch-real`.

Per source, over every message of its range:
- class: every message is a known type of its specification length (0 UNKNOWN, 0 MALFORMED);
- header: timestamps never go back; locate 0 exactly for the types the specification says
  "always 0"; every other locate was announced by an earlier Stock Directory (R) message, once;
  every stock field of a message with a locate equals the directory's symbol for it (K carries
  a stock with locate 0);
- orders, through normalize() (the field mapping the RTL is checked against): every reference
  of E, C, X, D and U names a live order added by A, F or U; executed and cancelled shares
  never exceed what is left (an order at 0 shares is gone); every new reference is new;
  prices of A, F and U are in 1..PRICE_MAX;
- MoldUDP64: the messages packed into packets of at most UDP_PAYLOAD_MAX bytes come out of the
  Mold model unchanged, in order, all OK;
- the counts (messages, per type, directory size, last timestamp) equal the manifest's expect.
A source without expect fails and prints the measured values as the TOML to pin.
"""

from collections import Counter

import pytest

from common.itch_real import Retired, fetch, load, messages
from model.itch import LOCATE_ZERO, PRICE_MAX, SPEC, ItchClass, classify, decode, header, normalize
from model.mold import HDR_LEN, UDP_PAYLOAD_MAX, FeedConfig, FeedState, MoldStatus, expect, pack

pytestmark = pytest.mark.real

SOURCES = load()
REFS = {"E", "C", "X", "D", "U"}  # types that name an existing order
HAS_STOCK = {t for t, s in SPEC.items() if any(f[0] == "stock" for f in s.fields)}


class MoldCheck:
    """Packs messages into MoldUDP64 packets and checks each packet against the Mold model."""

    def __init__(self) -> None:
        self.cfg = FeedConfig()
        self.state = FeedState()
        self.seq = 1
        self.batch: list[bytes] = []
        self.size = 0
        self.packets = 0
        self.out = 0

    def add(self, msg: bytes) -> None:
        if self.size + 2 + len(msg) > UDP_PAYLOAD_MAX - HDR_LEN:
            self.flush()
        self.batch.append(msg)
        self.size += 2 + len(msg)

    def flush(self) -> None:
        if not self.batch:
            return
        res, self.state = expect(pack(self.cfg.session, self.seq, self.batch), False, 0, self.cfg, self.state)
        assert res.status == MoldStatus.OK, f"packet {self.packets} (seq {self.seq}): {res.status.name}"
        assert res.gap == 0 and res.oversize == 0 and res.dup_msgs == 0
        assert res.msgs == tuple(enumerate(self.batch, self.seq)), f"packet {self.packets}: messages changed"
        self.packets += 1
        self.out += len(self.batch)
        self.seq += len(self.batch)
        self.batch, self.size = [], 0


def check(msgs) -> dict:
    types: Counter[str] = Counter()
    directory: dict[int, bytes] = {}
    live: dict[int, int] = {}  # order reference -> shares left
    mold = MoldCheck()
    last_ts = n = 0
    for msg in msgs:
        t = chr(msg[0])
        where = f"message {n} ({t})"
        assert classify(msg[0], len(msg)) not in (ItchClass.UNKNOWN, ItchClass.MALFORMED), f"{where}: {len(msg)} B"
        locate, _, ts = header(msg)
        assert ts >= last_ts, f"{where}: timestamp {ts} < {last_ts}"
        last_ts = ts
        fields = decode(msg)
        if t in LOCATE_ZERO:
            assert locate == 0, f"{where}: locate {locate}"
        elif t == "R":
            assert locate not in directory and locate != 0, f"{where}: locate {locate} announced again"
            directory[locate] = fields["stock"]
        else:
            assert locate in directory, f"{where}: locate {locate} not in the directory"
        if t in HAS_STOCK and locate != 0:  # K: a stock field, locate always 0
            assert fields["stock"] == directory[locate], f"{where}: {fields['stock']} for locate {locate}"

        ev = normalize(msg)
        if t in REFS:
            ref = ev["order_ref"]
            assert ref in live, f"{where}: order {ref} is not live"
            if t in "DU":
                del live[ref]
            else:
                assert ev["shares"] <= live[ref], f"{where}: {ev['shares']} shares of {live[ref]}"
                live[ref] -= ev["shares"]
                if live[ref] == 0:
                    del live[ref]
        if t in "AFU":
            new = ev["new_ref"] if t == "U" else ev["order_ref"]
            assert new not in live, f"{where}: order {new} added twice"
            assert 0 < ev["price"] <= PRICE_MAX, f"{where}: price {ev['price']}"
            live[new] = ev["shares"]
        mold.add(msg)
        types[t] += 1
        n += 1
    mold.flush()
    assert mold.out == n
    return {"msgs": n, "last_timestamp": last_ts, "directory": len(directory), "packets": mold.packets,
            "types": dict(sorted(types.items()))}  # fmt: skip


def toml(measured: dict) -> str:
    types = ", ".join(f"{k} = {v}" for k, v in measured["types"].items())
    scalars = "".join(f"{k} = {v}\n" for k, v in measured.items() if k != "types")
    return f"[source.expect]\n{scalars}types = {{ {types} }}\n"


@pytest.mark.parametrize("name", SOURCES)
def test_itch_real_model(name: str):
    src = SOURCES[name]
    try:
        path = fetch(src)
    except Retired as e:
        pytest.skip(str(e))
    measured = check(messages(path))
    assert src.expect == measured, f"{name}: measured values differ from the manifest:\n{toml(measured)}"
