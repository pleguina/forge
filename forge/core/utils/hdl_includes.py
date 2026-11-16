"""The `` `include `` closure of Verilog sources.

Both the simulator build key and the ipbb exporter need every header a
source pulls in, through any depth of nesting: a header missing from the key
lets a stale build be reused, and one missing from an export leaves a
component that does not compile on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

from forge.core.utils.hdl_parser import _strip_sv_comments

_INCLUDE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)
VERILOG_SUFFIXES = {".v", ".sv", ".vh", ".svh", ".h", ".inc"}


@dataclass(frozen=True)
class Include:
    """One `` `include "spelling" `` in *includer*; *path* is None when no
    search directory holds it."""

    includer: Path
    spelling: str
    path: Optional[Path]


def include_tree(sources: Iterable[Path], search: Sequence[Path] = ()) -> List[Include]:
    """Every include reachable from *sources*, depth first, each header once.

    A header is looked up next to the file that includes it, then in
    *search* in order, which is how xvlog and Verilator resolve it.
    """
    found: List[Include] = []
    seen: Set[Path] = set()

    def visit(path: Path) -> None:
        if path.suffix.lower() not in VERILOG_SUFFIXES:
            return
        try:
            text = _strip_sv_comments(path.read_text(errors="replace"))
        except OSError:
            return
        for spelling in _INCLUDE.findall(text):
            target = next(
                (d / spelling for d in (path.parent, *search) if (d / spelling).is_file()),
                None,
            )
            found.append(Include(path, spelling, target.resolve() if target else None))
            if target is not None and target.resolve() not in seen:
                seen.add(target.resolve())
                visit(target.resolve())

    for source in sources:
        resolved = Path(source).resolve()
        seen.add(resolved)
        visit(resolved)
    return found
