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

import hashlib
import json
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from forge.core.utils.hdl_includes import include_tree

_HDL_SUFFIXES = {".v": "xvlog", ".sv": "xvlog --sv", ".vhd": "xvhdl", ".vhdl": "xvhdl"}
_HEADER_SUFFIXES = {".vh", ".svh", ".h"}
OWNED_DIR = "forge"                      # under firmware/hdl, written and replaced by FORGE only
OWNER_FILE = ".forge_component.json"
_DEP_MARK = "# Written by forge from"


def _sources(manifest: dict) -> List[Tuple[str, Path]]:
    """(directory in the component, source) in compile order: module sources, the top, its VHDL entity."""
    missing = [m["top"] for m in manifest["modules"].values() if not m["verilog_files"]]
    if missing:
        raise ValueError(
            f"the build manifest lists no RTL for {', '.join(missing)}; build their HLS "
            "RTL and pass its directory to forge build --build-dir"
        )
    top, *rest = manifest["verilog_files"]
    owner = {f: name for name, m in manifest["modules"].items() for f in m["verilog_files"]}
    out = [(owner.get(f, "support"), Path(f)) for f in rest]
    out.append(("top", Path(top)))
    out += [("top", Path(f)) for f in manifest.get("vhdl_files", [])]
    for _, src in out:
        if src.suffix not in _HDL_SUFFIXES:
            raise ValueError(f"{src}: an ipbb component carries Verilog, SystemVerilog and VHDL sources, "
                             f"not {src.suffix or 'files without a suffix'}")
    return out


def _headers(source: Path, rel: str, search: List[Path]) -> List[Tuple[str, Path]]:
    """``(component path, header)`` for every header *source* includes at any depth.

    *rel* is where *source* lands in the component. Each header goes where its
    `` `include `` spelling points from the file that includes it, so the copy
    compiles without the build's include directories.
    """
    placed: Dict[Path, str] = {source.resolve(): rel}
    found: List[Tuple[str, Path]] = []
    for inc in include_tree([source], search):
        if inc.path is None:
            raise ValueError(
                f"{inc.includer}: `include \"{inc.spelling}\" is in none of the build's include directories")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(placed[inc.includer]), inc.spelling))
        if target.startswith("../") or posixpath.isabs(target):
            raise ValueError(
                f"{inc.includer}: `include \"{inc.spelling}\" points outside the component")
        placed.setdefault(inc.path, target)
        found.append((placed[inc.path], inc.path))
    return found


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_component(manifest_path: Path, component_dir: Path, dep_name: Optional[str] = None) -> Path:
    """Copy *manifest_path*'s compile list into *component_dir* and write its dep file.

    The sources go under ``firmware/hdl/forge/<module>/`` (``top``, one directory per
    module, ``support`` for FORGE's helpers), with the headers they include, and
    ``firmware/hdl/forge/.forge_component.json`` records each file's hash. Only that
    directory and the dep file FORGE wrote are replaced; anything else in the
    component is left alone, and a directory or dep file FORGE did not write is an
    error. Returns the dep file; *dep_name* defaults to ``<top>.dep``, ``<top>`` being
    the VHDL entity in ``--mode vhdl`` and the Verilog module otherwise.
    """
    manifest = json.loads(manifest_path.read_text())
    sources = _sources(manifest)
    search = [Path(d) for d in manifest.get("include_dirs", [])]
    files: Dict[str, Path] = {}
    for subdir, src in sources:
        rel_src = f"{subdir}/{src.name}"
        for rel, path in [(rel_src, src), *_headers(src, rel_src, search)]:
            if rel in files and files[rel] != path and _sha256(files[rel]) != _sha256(path):
                raise ValueError(f"two different files would both be {OWNED_DIR}/{rel}: {files[rel]} and {path}")
            files.setdefault(rel, path)

    hdl = component_dir / "firmware" / "hdl"
    owned = hdl / OWNED_DIR
    if owned.exists() and not (owned / OWNER_FILE).is_file():
        raise ValueError(f"{owned} exists and was not written by forge; move it away first")
    top = Path((manifest.get("vhdl_files") or [manifest["algorithm_top"]])[0]).stem
    dep = component_dir / "firmware" / "cfg" / (dep_name or f"{top}.dep")
    if dep.exists() and not dep.read_text().startswith(_DEP_MARK):
        raise ValueError(f"{dep} exists and was not written by forge; move it away first")

    hdl.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".forge-", dir=hdl))
    try:
        for rel, path in files.items():
            (staging / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, staging / rel)
        record = {
            "schema": 1,
            "build_manifest": str(manifest_path.resolve()),
            "build_manifest_sha256": _sha256(manifest_path),
            "files": {rel: {"sha256": _sha256(staging / rel),
                            "kind": "header" if Path(rel).suffix in _HEADER_SUFFIXES else
                            {".v": "verilog", ".sv": "systemverilog"}.get(Path(rel).suffix, "vhdl")}
                      for rel in sorted(files)},
        }
        (staging / OWNER_FILE).write_text(json.dumps(record, indent=2) + "\n")
        if owned.exists():
            shutil.rmtree(owned)
        staging.rename(owned)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    dep.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{_DEP_MARK} {manifest_path.name}; do not edit."]
    # ipbb compiles a dep file's entries last to first.
    lines += [f"src {OWNED_DIR}/{subdir}/{src.name}" for subdir, src in reversed(sources)]
    dep.write_text("\n".join(lines) + "\n")
    return dep


_VHDL_UNIT = re.compile(r"^\s*(?:package|entity)\s+(\w+)\s+is\b", re.I | re.M)
_VHDL_USE = re.compile(r"^\s*use\s+(\w+)\.(\w+)", re.I | re.M)
_VHDL_INST = re.compile(r"\bentity\s+(\w+)\.(\w+)", re.I)


def _vhdl_order(files: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """*files* (path, tool, library) with each VHDL file after the files declaring the
    packages and entities it uses from its own library, ipbb's order kept otherwise.

    ipbb reads .d3 files first to last and .dep files last to first, and the tools
    ipbb drives order VHDL by dependency themselves; XSim compiles in the order given.
    """
    declares: Dict[Tuple[str, str], int] = {}
    uses: List[set] = []
    for i, (path, tool, lib) in enumerate(files):
        if not tool.startswith("xvhdl"):
            uses.append(set())
            continue
        try:
            text = re.sub(r"--[^\n]*", "", Path(path).read_text(errors="replace"))
        except OSError:
            text = ""
        for unit in _VHDL_UNIT.findall(text):
            declares.setdefault((lib, unit.lower()), i)
        refs = {(lib if ref.lower() == "work" else ref.lower(), unit.lower())
                for ref, unit in _VHDL_USE.findall(text) + _VHDL_INST.findall(text)}
        uses.append(refs)
    deps = [{declares[r] for r in refs if r in declares and declares[r] != i} for i, refs in enumerate(uses)]
    done: List[int] = []
    placed: set = set()
    while len(done) < len(files):
        ready = next((i for i in range(len(files)) if i not in placed and deps[i] <= placed), None)
        if ready is None:   # a cycle: keep ipbb's order for the rest
            ready = next(i for i in range(len(files)) if i not in placed)
        placed.add(ready)
        done.append(ready)
    return [files[i] for i in done]


def xsim_compile_script(src_dir: Path, component: str, depfile: str, *, toolset: str = "sim") -> str:
    """xvhdl/xvlog commands for the dep tree rooted at *component* (``package:path``)'s *depfile*.

    Files keep ipbb's library and VHDL-2008 flag, and its order except that a VHDL
    file comes after the ones declaring what it uses; consecutive files sharing a
    compiler, library and standard are compiled in one call.
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

    files: List[Tuple[str, str, str]] = []
    for src in parser.commands["src"]:
        tool = _HDL_SUFFIXES.get(Path(src.filepath).suffix)
        if not src.useInSim or tool is None:
            continue
        if tool == "xvhdl" and src.vhdl2008:
            tool = "xvhdl --2008"
        files.append((src.filepath, tool, src.lib or "xil_defaultlib"))

    calls: List[Tuple[str, List[str]]] = []
    for filepath, tool, lib in _vhdl_order(files):
        head = f"{tool} -work {lib}"
        if calls and calls[-1][0] == head:
            calls[-1][1].append(filepath)
        else:
            calls.append((head, [filepath]))
    return "\n".join(["set -e"] + [f"{head} {' '.join(files)}" for head, files in calls]) + "\n"
