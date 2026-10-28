#!/usr/bin/env python3
"""Generate the event descriptor outputs from firmware/formats/event_desc.toml.

    scripts/gen_desc.py          write into the repository
    scripts/gen_desc.py DIR      write the same tree under DIR (used by just gen-check)
    scripts/gen_desc.py --list   print the output paths, one per line (just gen-check diffs them)

Skeleton: it checks the schema version only. The entry kinds, the fields and the three outputs
(SV package, C++ header, Python model) come in S1.
"""

import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "firmware" / "formats" / "event_desc.toml"
OUTPUTS: dict[str, str] = {}  # repository path -> text


def load(path: Path) -> dict:
    schema = tomllib.loads(path.read_text(encoding="utf-8"))
    version = schema.get("version")
    if type(version) is not int or not 1 <= version <= 15:
        sys.exit(f"{path}: version must be an integer in 1..15 (4 bits of ctrl), got {version!r}")
    return schema


def main(argv: list[str]) -> None:
    if argv == ["--list"]:
        for rel in OUTPUTS:
            print(rel)
        return
    if len(argv) > 1:
        sys.exit(__doc__)
    out = Path(argv[0]) if argv else REPO
    load(SCHEMA)
    for rel, text in OUTPUTS.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1:])
