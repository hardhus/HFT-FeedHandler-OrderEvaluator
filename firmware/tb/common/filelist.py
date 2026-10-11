"""Parse .f filelists: '#'/'//' comments, blank lines, paths relative to the .f file."""

from pathlib import Path


def read_filelist(path: Path) -> list[Path]:
    path = Path(path)
    out: list[Path] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        out.append((path.parent / line).resolve())
    return out
