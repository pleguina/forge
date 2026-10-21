"""forge.hls.stage_stamp — content-hash stamps for ``forge hls run`` stages.

Output files show that a stage ran once, not that it ran on the current
sources. A stage counts as done only when the stamp written after its last
successful run matches the hash of what the stage reads now:

  * the generated TCL scripts of the stage (and ``project.tcl``);
  * every absolute file path named in those scripts — sources, testbenches,
    testbench-argument data files;
  * headers under each ``-I`` include directory and next to each source;
  * files listed in a ``.txt`` data manifest, one path per line relative to
    the manifest (``#`` comments and blank lines ignored);
  * the resolved ``vitis_hls`` executable path, which carries the tool
    version in a standard Xilinx install.

Anything under the build root or the IP packages directory is an output and
is never hashed. Stamps live in ``<build_root>/<module>/.forge_stamps/``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterable, List, Optional

from forge.core.utils.content_hash import hash_bytes, hash_file

STAMP_SCHEMA_VERSION = 1
STAMP_DIR_NAME = ".forge_stamps"

STAGE_SCRIPTS = {
    "csim": ("project.tcl", "csim.tcl"),
    "synth": ("project.tcl", "synth.tcl"),
    "cosim": ("project.tcl", "synth.tcl", "cosim.tcl"),
    "export": ("project.tcl", "synth.tcl", "ip_export.tcl"),
}

HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".tpp", ".ipp", ".inc"}

_INCLUDE_DIR_RE = re.compile(r"-I(/[^\s\"{}]+)")
_ABS_PATH_RE = re.compile(r"(?<![\w.\-/])(/[^\s\"{}]+)")


def _is_under(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def _headers_in(directory: Path, recursive: bool) -> List[Path]:
    if not directory.is_dir():
        return []
    walker = directory.rglob("*") if recursive else directory.iterdir()
    return [p for p in walker if p.is_file() and p.suffix in HEADER_SUFFIXES]


def _manifest_entries(manifest: Path) -> List[Path]:
    entries = []
    for line in manifest.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line) if os.path.isabs(line) else manifest.parent / line
        if candidate.is_file():
            entries.append(candidate.resolve())
    return entries


def resolve_tool(executable: str) -> str:
    """Resolved path of *executable*, or the name as given if not on PATH."""
    found = shutil.which(executable)
    return os.path.realpath(found) if found else executable


def stage_inputs(
    module_dir: Path, step: str, output_roots: Iterable[Path]
) -> List[Path]:
    """Every file *step* of the module in *module_dir* reads, sorted.

    Raises FileNotFoundError if one of the stage's TCL scripts is missing.
    """
    output_roots = [Path(r).resolve() for r in output_roots]
    inputs = set()
    for name in STAGE_SCRIPTS[step]:
        script = module_dir / name
        inputs.add(script.resolve())
        text = script.read_text(errors="replace")

        for match in _INCLUDE_DIR_RE.finditer(text):
            include_dir = Path(match.group(1)).resolve()
            if not _is_under(include_dir, output_roots):
                inputs.update(h.resolve() for h in _headers_in(include_dir, recursive=True))

        for match in _ABS_PATH_RE.finditer(text):
            path = Path(match.group(1))
            if not path.is_file():
                continue
            path = path.resolve()
            if _is_under(path, output_roots):
                continue
            inputs.add(path)
            if path.suffix == ".txt":
                inputs.update(_manifest_entries(path))
            else:
                inputs.update(h.resolve() for h in _headers_in(path.parent, recursive=False))

    return sorted(inputs)


def compute_stage_hash(
    module_dir: Path, step: str, output_roots: Iterable[Path], tool: str
) -> str:
    files = stage_inputs(module_dir, step, output_roots)
    lines = [f"tool:{tool}"] + [f"{p}:{hash_file(p)}" for p in files]
    return hash_bytes("\n".join(lines).encode("utf-8"))


def stamp_path(module_dir: Path, step: str) -> Path:
    return module_dir / STAMP_DIR_NAME / f"{step}.json"


def read_stamp(module_dir: Path, step: str) -> Optional[dict]:
    path = stamp_path(module_dir, step)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if data.get("schema_version") != STAMP_SCHEMA_VERSION:
        return None
    return data


def write_stamp(module_dir: Path, step: str, inputs_hash: str, tool: str) -> None:
    path = stamp_path(module_dir, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": STAMP_SCHEMA_VERSION,
        "stage": step,
        "inputs_hash": inputs_hash,
        "tool": tool,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def clear_stamp(module_dir: Path, step: str) -> None:
    stamp_path(module_dir, step).unlink(missing_ok=True)


def clear_all_stamps(module_dir: Path) -> None:
    for step in STAGE_SCRIPTS:
        clear_stamp(module_dir, step)
    clear_stamp(module_dir, "project")
