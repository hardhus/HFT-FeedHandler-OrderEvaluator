"""Offline tests of the real-data fetcher (common/itch_real.py, audit/12 S11).

No network: fetch() gets a fake download that writes given bytes, and the data is a small
gzip'd BinaryFile stream built here.
"""

import gzip
import hashlib
import random
from pathlib import Path

import pytest

from common.itch_real import Retired, Source, fetch, load, messages, path_of

GOOD_SHA = "0" * 64


def binary_file(msgs: list[bytes]) -> bytes:
    return b"".join(len(m).to_bytes(2, "big") + m for m in msgs)


def some_msgs(rng: random.Random, n: int) -> list[bytes]:
    return [bytes([rng.choice(b"ADEXUPR")]) + rng.randbytes(rng.randint(5, 49)) for _ in range(n)]


def source(data: bytes, status: str = "active", note: str = "") -> Source:
    return Source("t", "https://example.invalid/x.gz", 0, len(data) - 1, hashlib.sha256(data).hexdigest(), status,
                  note)  # fmt: skip


def test_manifest():
    """The committed manifest loads, and every source names a Nasdaq file and a range that
    starts at the stream's start (messages() needs the gzip header)."""
    sources = load()
    assert sources
    for src in sources.values():
        assert src.url.startswith("https://emi.nasdaq.com/ITCH/"), src.name
        assert src.first == 0, src.name


@pytest.mark.parametrize(
    "entry,error",
    [
        ('status = "gone"', "status"),
        ('sha256 = "abc"', "SHA-256"),
        ('status = "retired"', "note"),
        ("range = [10, 5]", "range"),
    ],
)
def test_manifest_rules(tmp_path: Path, entry: str, error: str):
    fields = {"name": '"a"', "url": '"https://emi.nasdaq.com/ITCH/a.gz"', "range": "[0, 99]",
              "sha256": f'"{GOOD_SHA}"', "status": '"active"'}  # fmt: skip
    key, value = entry.split(" = ")
    fields[key] = value
    p = tmp_path / "m.toml"
    p.write_bytes(("[[source]]\n" + "".join(f"{k} = {v}\n" for k, v in fields.items())).encode("utf-8"))
    with pytest.raises(ValueError, match=error):
        load(p)


def test_manifest_duplicate_name(tmp_path: Path):
    one = f'[[source]]\nname = "a"\nurl = "u"\nrange = [0, 9]\nsha256 = "{GOOD_SHA}"\nstatus = "active"\n'
    p = tmp_path / "m.toml"
    p.write_bytes((one + one).encode("utf-8"))
    with pytest.raises(ValueError, match="twice"):
        load(p)


def test_messages_of_a_cut_stream(tmp_path: Path):
    """A range of the compressed file gives a prefix of the messages, each one complete, for
    cuts anywhere (in the gzip header, mid-block, mid-message); the whole file gives all."""
    rng = random.Random(1)
    msgs = some_msgs(rng, 20000)
    gz = gzip.compress(binary_file(msgs), mtime=0)
    p = tmp_path / "x.gz.part"
    cuts = [1, 10, 11, 100, *rng.sample(range(100, len(gz)), 30), len(gz) - 8]
    for cut in cuts:
        p.write_bytes(gz[:cut])
        got = list(messages(p))
        assert got == msgs[: len(got)], cut
    p.write_bytes(gz)
    assert list(messages(p)) == msgs


def test_fetch(tmp_path: Path):
    """Downloads once, checks the hash, re-downloads a file that does not match, and fails on
    a download that does not match the manifest."""
    data = b"range bytes"
    src = source(data)
    calls = []

    def get(s: Source, dest: Path, payload: bytes = data) -> None:
        calls.append(s.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)

    p = fetch(src, tmp_path, get)
    assert p == path_of(src, tmp_path) and p.read_bytes() == data and len(calls) == 1
    assert fetch(src, tmp_path, get) == p and len(calls) == 1  # kept, not downloaded again
    p.write_bytes(b"stale")
    assert fetch(src, tmp_path, get).read_bytes() == data and len(calls) == 2
    p.unlink()
    with pytest.raises(RuntimeError, match="changed"):
        fetch(src, tmp_path, lambda s, d: get(s, d, b"other bytes"))


def test_fetch_retired(tmp_path: Path):
    """A retired source is never downloaded; the error carries its note."""
    src = source(b"x", "retired", "removed from emi.nasdaq.com on 2027-01-01")

    def get(s: Source, dest: Path) -> None:
        raise AssertionError("downloaded a retired source")

    with pytest.raises(Retired, match="2027-01-01"):
        fetch(src, tmp_path, get)
