"""Real Nasdaq TotalView-ITCH 5.0 data for the real-data checks (audit/12 S11).

Decision (user, 2026-10-28): the data never enters the repository, its history, a CI cache or
an artifact. The repository holds only the manifest (firmware/tb/data/itch_real.toml): per
source the Nasdaq URL, a byte range of the compressed file, the SHA-256 of that range and the
expected results. fetch() downloads the range straight from Nasdaq into the build directory
and checks the hash, so every machine reads the same bytes and gets the same results. A source
whose URL is gone stays in the manifest as "retired", with its hash and results, and is not
downloaded.

Format: the Nasdaq files are gzip'd BinaryFile streams, each message a 2-byte big-endian
length and the message. A range of the compressed file is the start of that stream: messages()
decompresses it and drops the cut gzip tail and the last, partial message.

Run as a script to print the SHA-256 of the range 0..LAST of a URL (pinning a new source):
    python firmware/tb/common/itch_real.py URL LAST
"""

import hashlib
import os
import sys
import tomllib
import urllib.request
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "firmware" / "tb" / "data" / "itch_real.toml"
BUILD = Path(os.environ.get("HFT_BUILD_DIR") or REPO / "build")
STATUSES = ("active", "retired")
CHUNK = 1 << 20


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    first: int  # byte range of the compressed file, inclusive
    last: int
    sha256: str
    status: str
    note: str = ""
    expect: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return self.last - self.first + 1


class Retired(Exception):
    """The source's URL is gone; its results stay in the manifest."""


def load(path: Path = MANIFEST) -> dict[str, Source]:
    with path.open("rb") as f:
        doc = tomllib.load(f)
    out: dict[str, Source] = {}
    for s in doc["source"]:
        first, last = s["range"]
        src = Source(s["name"], s["url"], first, last, s["sha256"].lower(), s["status"], s.get("note", ""),
                     s.get("expect", {}))  # fmt: skip
        if src.name in out:
            raise ValueError(f"{path.name}: source {src.name} twice")
        if src.status not in STATUSES:
            raise ValueError(f"{src.name}: status {src.status!r}, not one of {STATUSES}")
        if not 0 <= first < last:
            raise ValueError(f"{src.name}: range {first}..{last}")
        if len(src.sha256) != 64 or any(c not in "0123456789abcdef" for c in src.sha256):
            raise ValueError(f"{src.name}: sha256 {src.sha256!r} is not a SHA-256")
        if src.status == "retired" and not src.note:
            raise ValueError(f"{src.name}: a retired source needs a note (when and why)")
        out[src.name] = src
    return out


def path_of(src: Source, build: Path = BUILD) -> Path:
    return build / "itch-real" / f"{src.name}.gz.part"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download(src: Source, dest: Path) -> None:
    """The source's byte range into dest (through a temporary file, so a broken download
    never leaves a file that looks complete)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    req = urllib.request.Request(src.url, headers={"Range": f"bytes={src.first}-{src.last}"})
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
        if r.status != 206:  # a server that ignores Range would send the whole file
            raise RuntimeError(f"{src.name}: HTTP {r.status} for a range request, expected 206")
        while chunk := r.read(CHUNK):
            f.write(chunk)
    if tmp.stat().st_size != src.size:
        raise RuntimeError(f"{src.name}: got {tmp.stat().st_size} bytes, the range is {src.size}")
    tmp.replace(dest)


def fetch(src: Source, build: Path = BUILD, get=download) -> Path:
    """The source's range in the build directory, checked against the manifest hash.
    Downloaded once; a file whose hash does not match is downloaded again, and a fresh
    download that does not match is an error (the file on the server changed)."""
    if src.status == "retired":
        raise Retired(f"{src.name}: retired: {src.note}")
    dest = path_of(src, build)
    if dest.is_file() and sha256_of(dest) == src.sha256:
        return dest
    get(src, dest)
    got = sha256_of(dest)
    if got != src.sha256:
        raise RuntimeError(f"{src.name}: SHA-256 {got}, the manifest says {src.sha256}: the file at {src.url} "
                           f"changed; retire this source and pin a new one (DEVELOPMENT.md)")  # fmt: skip
    return dest


def messages(path: Path) -> Iterator[bytes]:
    """Every complete message of a gzip'd BinaryFile prefix, in order."""
    d = zlib.decompressobj(wbits=31)  # gzip header
    buf = bytearray()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            buf += d.decompress(chunk)
            i = 0
            while i + 2 <= len(buf):
                n = int.from_bytes(buf[i : i + 2], "big")
                if i + 2 + n > len(buf):
                    break
                yield bytes(buf[i + 2 : i + 2 + n])
                i += 2 + n
            del buf[:i]


if __name__ == "__main__":
    url, last = sys.argv[1], int(sys.argv[2])
    src = Source("pin", url, 0, last, "0" * 64, "active")
    tmp = path_of(src).with_suffix(".pin")
    download(src, tmp)
    print(f"range = [0, {last}]\nsha256 = \"{sha256_of(tmp)}\"")
    tmp.unlink()
