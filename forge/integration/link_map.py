"""Which link carries what into which algorithm port, and which build is doing it.

For commissioning and online software (SWATCH, HERD): one JSON with every
resolved link (fibre, cage, quad, lane, protocol, logical input, algorithm
port), the register layout when a map is given, and the identity of the build
those describe, so that "what code, mapping and firmware runs on this board"
has one answer and an event can be replayed against the same inputs.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from forge.integration.registers import RegisterMap

_LINK_KEYS = ("endpoint_id", "direction", "slr", "gt_site", "lane", "lane_width", "protocol", "quad_type",
              "cage", "fiber", "polarity", "logical_id", "logical_name", "link_function", "endpoint_role")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def build_identity(files: Mapping[str, Path], repo: Optional[Path] = None) -> Dict[str, Any]:
    """Content hashes of *files*, the commit of *repo*, FORGE's version, and one id over all of them.

    ``build_id32`` is the first 32 bits of ``build_id``, small enough for a
    status register, so the firmware can say which build it is.
    """
    from forge import __version__

    ident: Dict[str, Any] = {"forge_version": __version__,
                             "files": {label: {"path": str(p), "sha256": _sha256(p)} for label, p in sorted(files.items())}}
    if repo is not None:
        commit = _git(repo, "rev-parse", "HEAD")
        ident["source"] = {"commit": commit,
                           "dirty": bool(_git(repo, "status", "--porcelain", "--untracked-files=no"))
                           if commit else None}
    digest = hashlib.sha256()
    digest.update(ident["forge_version"].encode())
    for label, f in ident["files"].items():
        digest.update(f"{label}={f['sha256']}".encode())
    if ident.get("source", {}).get("commit"):
        digest.update(f"commit={ident['source']['commit']} dirty={ident['source']['dirty']}".encode())
    ident["build_id"] = digest.hexdigest()
    ident["build_id32"] = f"0x{ident['build_id'][:8]}"
    return ident


def links(resolved: Mapping) -> List[Dict[str, Any]]:
    out = []
    for r in resolved.get("detector_inputs", []):
        out.append({"name": r["name"], "detector": r.get("detector"),
                    **{k: r["endpoint"].get(k) for k in _LINK_KEYS},
                    "algo_port": (r.get("output") or {}).get("algo_port"),
                    "frontend_instance": (r.get("frontend") or {}).get("instance")})
    for r in resolved.get("trigger_outputs", []):
        out.append({"name": r["name"], **{k: r["endpoint"].get(k) for k in _LINK_KEYS},
                    "source": f"{r['source']['instance']}.{r['source']['port']}"})
    return out


def link_map(resolved: Mapping, identity: Mapping[str, Any], *, clock: Optional[str] = None,
             registers: Optional[RegisterMap] = None, word_bits: int = 32) -> Dict[str, Any]:
    doc: Dict[str, Any] = {"schema": "forge.link_map/1", "build": dict(identity), "links": links(resolved)}
    if clock:
        doc["link_clock"] = clock
    if registers is not None:
        doc["registers"] = {"word_bits": word_bits, "layout": [
            {"name": r.name, "kind": r.kind, "first_word": first, "words": n, "width": r.width,
             "access": "rw" if r.writable else "r", "reset": r.reset,
             "fields": [{"name": f.name, "bits": [f.hi, f.lo]} for f in r.fields]}
            for r, first, n in registers.layout(word_bits)]}
    return doc
