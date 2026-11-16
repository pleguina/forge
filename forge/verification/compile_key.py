"""Identity of a simulator build, so that a reused build is the one its inputs describe.

A backend that compiles once and runs many events reuses its build only when
the key stored next to it is the key of the current inputs: the compile list
and every source it names (by content), every header those sources include at
any depth, the HDL headers in every include directory, the commands with their
flags, and the simulator's version. Data
the testbench loads at run time (``$readmemh`` files) is not part of it.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Sequence

from forge.core.utils.hdl_includes import include_tree

_HEADERS = {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl", ".h", ".inc"}


@lru_cache(maxsize=None)
def tool_version(executable: str) -> str:
    try:
        out = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=60)
        return (out.stdout or out.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return f"{executable}: no version"


def _include_dirs(list_text: str, commands: Sequence[Sequence[str]]) -> List[Path]:
    dirs = [Path(d) for d in re.findall(r'^-I"([^"]+)"', list_text, re.M)]
    for cmd in commands:
        for i, tok in enumerate(cmd):
            if tok in ("--include", "-i") and i + 1 < len(cmd):
                dirs.append(Path(cmd[i + 1]))
            elif tok.startswith("-I") and len(tok) > 2:
                dirs.append(Path(tok[2:].strip('"')))
    return dirs


def _file(h, path: Path) -> None:
    h.update(str(path).encode() + b"\0")
    h.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"<missing>")


def build_key(list_file: Path, commands: Sequence[Sequence[str]], tools: Iterable[str]) -> str:
    """sha256 of everything that decides what *commands* build from *list_file*."""
    h = hashlib.sha256()
    for tool in tools:
        h.update(tool_version(tool).encode() + b"\0")
    for cmd in commands:
        h.update("\0".join(cmd).encode() + b"\n")
    text = list_file.read_text() if list_file.is_file() else ""
    h.update(text.encode())
    sources = [
        Path(path) for path in re.findall(r'"([^"]+)"', text)
        if not re.search(rf'^-I"{re.escape(path)}"', text, re.M)
    ]
    for path in sources:
        _file(h, path)
    include_dirs = sorted(set(_include_dirs(text, commands)))
    for inc in include_tree(sources, include_dirs):
        h.update(f"{inc.includer}\0{inc.spelling}\0".encode())
        if inc.path is None:
            h.update(b"<unresolved>")
        else:
            _file(h, inc.path)
    # Headers a source reaches only through a macro-built `include name.
    for d in include_dirs:
        for f in sorted(d.glob("*")) if d.is_dir() else []:
            if f.suffix in _HEADERS:
                _file(h, f)
    return h.hexdigest()


def reusable(sentinel: Path, key: str) -> bool:
    return sentinel.is_file() and sentinel.read_text().strip() == key
