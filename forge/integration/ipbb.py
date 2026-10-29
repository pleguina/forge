"""A FORGE build as an ipbb component, and ipbb dep trees compiled in XSim.

ipbb (https://github.com/ipbus/ipbb) builds a firmware project from ``.dep``
files that name sources by package and component. ``write_component`` copies
a build's compile list into ``<component>/firmware/hdl`` and writes
``<component>/firmware/cfg/<top>.dep``, so any ipbb project, EMP payloads
among them, includes the generated top with
``include -c <package>:<component> <top>.dep`` and never needs FORGE.

ipbb's own simulation flow drives Questa; ``xsim_compile_script`` turns the
same dep tree into xvhdl/xvlog commands. It needs ipbb importable.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

_HDL_SUFFIXES = {".v": "xvlog", ".sv": "xvlog --sv", ".vhd": "xvhdl", ".vhdl": "xvhdl"}


def _compile_order(manifest: dict) -> List[Path]:
    """Module sources, then the generated top, then its VHDL entity."""
    missing = [m["top"] for m in manifest["modules"].values() if not m["verilog_files"]]
    if missing:
        raise ValueError(
            f"the build manifest lists no RTL for {', '.join(missing)}; build their HLS "
            "RTL and pass its directory to forge build --build-dir"
        )
    top, *modules = manifest["verilog_files"]
    return [Path(p) for p in [*modules, top, *manifest.get("vhdl_files", [])]]


def write_component(manifest_path: Path, component_dir: Path, dep_name: Optional[str] = None) -> Path:
    """Copy *manifest_path*'s compile list into *component_dir* and write its dep file.

    Returns the dep file. *dep_name* defaults to ``<top>.dep``, ``<top>``
    being the VHDL entity in ``--mode vhdl`` and the Verilog module otherwise.
    """
    manifest = json.loads(manifest_path.read_text())
    sources = _compile_order(manifest)
    names = [src.name for src in sources]
    clashes = sorted({n for n in names if names.count(n) > 1})
    if clashes:
        raise ValueError(f"sources with the same file name cannot share one component: {', '.join(clashes)}")

    hdl = component_dir / "firmware" / "hdl"
    if hdl.exists():
        shutil.rmtree(hdl)
    hdl.mkdir(parents=True)
    for src in sources:
        shutil.copy2(src, hdl / src.name)

    top = Path((manifest.get("vhdl_files") or [manifest["algorithm_top"]])[0]).stem
    dep = component_dir / "firmware" / "cfg" / (dep_name or f"{top}.dep")
    dep.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Written by forge from {manifest_path.name}; do not edit."]
    # ipbb compiles a dep file's entries last to first.
    lines += [f"src {name}" for name in reversed(names)]
    dep.write_text("\n".join(lines) + "\n")
    return dep


def xsim_compile_script(src_dir: Path, component: str, depfile: str, *, toolset: str = "sim") -> str:
    """xvhdl/xvlog commands for the dep tree rooted at *component* (``package:path``)'s *depfile*.

    Files keep ipbb's order, library and VHDL-2008 flag; consecutive files
    sharing all three are compiled in one call.
    """
    try:
        from ipbb.depparser import DepFileParser, Pathmaker  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("ipbb is not installed; see https://github.com/ipbus/ipbb") from exc

    package, _, path = component.partition(":")
    parser = DepFileParser(toolset, Pathmaker(str(src_dir.resolve()), 0))
    parser.parse(package, path, depfile)
    if parser.unresolved:
        raise ValueError(f"unresolved dep entries: {parser.unresolved}")

    calls: List[Tuple[str, List[str]]] = []
    for src in parser.commands["src"]:
        tool = _HDL_SUFFIXES.get(Path(src.filepath).suffix)
        if not src.useInSim or tool is None:
            continue
        if tool == "xvhdl" and src.vhdl2008:
            tool = "xvhdl --2008"
        head = f"{tool} -work {src.lib or 'xil_defaultlib'}"
        if calls and calls[-1][0] == head:
            calls[-1][1].append(src.filepath)
        else:
            calls.append((head, [src.filepath]))
    return "\n".join(["set -e"] + [f"{head} {' '.join(files)}" for head, files in calls]) + "\n"
