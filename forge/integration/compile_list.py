"""The sources a project already compiles, read from the description it keeps.

A project keeps its compile description in its build system: a simulator
compile list (``.f``), an ipbb dep tree, or Hog list files. FORGE reads it
from there rather than asking for a second list, keeping per file what the
simulator needs: language, logical library and VHDL standard, in compile
order, with include directories and defines.

``verification.sources`` in ``forge.yml`` names the provider::

    sources: {provider: filelist, path: sim/top.f}
    sources: {provider: ipbb, src: src, component: "emp-fwk:boards/serenity", dep: top.dep}
    sources: {provider: hog, project: algo}        # Top/algo/list/*.src under the repository

Hog list files are read as Hog documents them: one library per ``.src``
file (named after it, or ``lib=``), paths relative to the repository root,
wildcards, nested list files, VHDL-2008 unless a file says ``93``,
``SystemVerilog`` for SystemVerilog, and ``nosim`` files left out. An IP
(``.xci``) or script is not a source a simulator compiles: it is listed in
``unresolved`` and the simulation of a design that needs it says so. ipbb
dep trees are parsed by ipbb itself, which must be importable.
"""

from __future__ import annotations

import glob
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

VHDL = (".vhd", ".vhdl")
VERILOG = (".v", ".vh")
SYSTEMVERILOG = (".sv", ".svh")
PROVIDERS = ("filelist", "ipbb", "hog")


@dataclass(frozen=True)
class SourceFile:
    path: Path
    language: str                 # vhdl, verilog or systemverilog
    library: str = "work"
    standard: Optional[str] = None    # 93 or 2008 for VHDL; None: the simulator's default

    def to_dict(self) -> Dict[str, Any]:
        return {"path": str(self.path), "language": self.language, "library": self.library,
                **({"standard": self.standard} if self.standard else {})}


@dataclass
class CompileList:
    files: List[SourceFile]
    include_dirs: List[Path] = field(default_factory=list)
    defines: Dict[str, str] = field(default_factory=dict)
    unresolved: List[str] = field(default_factory=list)    # entries no simulator compiles (IP, scripts)
    provenance: Dict[str, Any] = field(default_factory=dict)

    @property
    def sources(self) -> List[Path]:
        return [f.path for f in self.files]

    @property
    def libraries(self) -> List[str]:
        return list(dict.fromkeys(f.library for f in self.files))

    def described(self) -> Dict[str, Dict[str, Any]]:
        """path -> language, library and standard, for files other than plain
        Verilog in ``work``."""
        return {str(f.path): {k: v for k, v in f.to_dict().items() if k != "path"}
                for f in self.files if f.library != "work" or f.standard or f.language == "vhdl"}


def language_of(path: Path, systemverilog: bool = False) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in VHDL:
        return "vhdl"
    if suffix in SYSTEMVERILOG or (systemverilog and suffix in VERILOG):
        return "systemverilog"
    if suffix in VERILOG:
        return "verilog"
    return None


def from_filelist(path: Path) -> CompileList:
    """A ``.f`` compile list (:func:`forge.verification.auto.read_filelist`)."""
    from forge.verification.auto import read_filelist

    sources, incs, defines = read_filelist(path)
    files = []
    unresolved = []
    for src in sources:
        lang = language_of(src)
        if lang is None:
            unresolved.append(str(src))
        else:
            files.append(SourceFile(src, lang))
    return CompileList(files, incs, defines, unresolved, {"provider": "filelist", "path": str(path.resolve()),
                                                          "sha256": _sha(path)})


def from_ipbb(src_dir: Path, component: str, depfile: str) -> CompileList:
    """An ipbb dep tree, as ipbb resolves it for simulation."""
    from forge.integration.ipbb import dep_tree_files

    files = []
    for path, tool, lib in dep_tree_files(src_dir, component, depfile):
        if tool.startswith("xvhdl"):
            files.append(SourceFile(Path(path), "vhdl", lib, "2008" if "--2008" in tool else None))
        else:
            files.append(SourceFile(Path(path), "systemverilog" if "--sv" in tool else "verilog", lib))
    return CompileList(files, provenance={"provider": "ipbb", "src": str(src_dir.resolve()),
                                          "component": component, "dep": depfile})


def from_hog(repo: Path, project: str) -> CompileList:
    """The simulation sources of Hog project *project*: ``Top/<project>/list/*.src``
    (and ``*.sim``), read as Hog reads them, VHDL ordered by what each file uses."""
    from forge.integration.ipbb import _vhdl_order

    repo = repo.resolve()
    list_dir = repo / "Top" / project / "list"
    if not list_dir.is_dir():
        raise ValueError(f"no Hog project {project!r}: {list_dir} does not exist")
    entries: List[Tuple[SourceFile, str]] = []
    unresolved: List[str] = []
    lists: Dict[str, str] = {}
    for top in sorted([*list_dir.glob("*.src"), *list_dir.glob("*.sim")]):
        _read_hog_list(top, top.stem, repo, entries, unresolved, lists, set())
    seen: Dict[Path, int] = {}
    files: List[SourceFile] = []
    for f, _ in entries:
        if f.path not in seen:
            seen[f.path] = len(files)
            files.append(f)
    tools = [(str(f.path), "xvhdl" if f.language == "vhdl" else "xvlog", f.library) for f in files]
    by_path = {str(f.path): f for f in files}
    ordered = [by_path[p] for p, _, _ in _vhdl_order(tools)]
    return CompileList(ordered, unresolved=unresolved,
                       provenance={"provider": "hog", "repository": str(repo), "project": project, "lists": lists})


def _read_hog_list(path: Path, library: str, repo: Path, out: List[Tuple[SourceFile, str]], unresolved: List[str],
                   lists: Dict[str, str], stack: set, base: Optional[Path] = None) -> None:
    if path in stack:
        raise ValueError(f"{path}: a Hog list file that includes itself")
    stack = stack | {path}
    lists[str(path)] = _sha(path)
    base = base or repo
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, *props = line.split()
        options = {p.partition("=")[0]: p.partition("=")[2] for p in props}
        if "nosim" in options:
            continue
        matches = sorted(glob.glob(str(base / name))) if any(c in name for c in "*?[") else [str(base / name)]
        if not matches:
            raise ValueError(f"{path}: {name} matches no file under {base}")
        for match in matches:
            target = Path(match)
            if target.suffix in (".src", ".sim"):
                sub_base = repo / options["path"] if options.get("path") else base
                _read_hog_list(target, options.get("lib") or target.stem, repo, out, unresolved, lists, stack,
                               sub_base)
                continue
            if target.suffix == ".ext":
                _read_hog_ext(target, library, out, unresolved, lists)
                continue
            if not target.is_file():
                raise ValueError(f"{path}: {name} is not a file under {base}")
            lang = language_of(target, "SystemVerilog" in options)
            if lang is None:
                unresolved.append(str(target))
                continue
            standard = ("93" if "93" in options else "2008") if lang == "vhdl" else None
            out.append((SourceFile(target.resolve(), lang, library if lang == "vhdl" else "work", standard), raw))


def _read_hog_ext(path: Path, library: str, out: List[Tuple[SourceFile, str]], unresolved: List[str],
                  lists: Dict[str, str]) -> None:
    """A Hog ``.ext`` list: absolute paths, each with the md5 the project expects."""
    import os

    lists[str(path)] = _sha(path)
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, *rest = line.split()
        target = Path(os.path.expandvars(name))
        if not target.is_absolute():
            target = Path(os.environ.get("HOG_EXTERNAL_PATH", "")) / target
        if rest and target.is_file() and hashlib.md5(target.read_bytes()).hexdigest() != rest[0]:
            raise ValueError(f"{path}: {target} does not have the md5 the list gives ({rest[0]})")
        lang = language_of(target)
        if lang is None or not target.is_file():
            unresolved.append(str(target))
        else:
            out.append((SourceFile(target, lang, library if lang == "vhdl" else "work",
                                   "2008" if lang == "vhdl" else None), raw))


def resolve(spec: Mapping[str, Any], root: Path) -> CompileList:
    """``verification.sources`` of a ``forge.yml`` rooted at *root*."""
    provider = str(spec.get("provider") or "")
    if provider == "filelist":
        return from_filelist(root / str(spec["path"]))
    if provider == "ipbb":
        return from_ipbb(root / str(spec.get("src", "src")), str(spec["component"]), str(spec["dep"]))
    if provider == "hog":
        return from_hog(root / str(spec.get("repository", ".")), str(spec["project"]))
    raise ValueError(f"verification.sources.provider: {provider!r}, want one of {', '.join(PROVIDERS)}")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
