"""A subsystem of a generated design, built and simulated on its own.

A ``scope: subsystem`` verification target names instances of the design;
:func:`subsystem_design` writes the design reduced to them, which
``forge test`` builds like any other, and :func:`subsystem_difference`
checks the result is wired exactly as those instances are in the design.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from forge.contracts.design_variant import load_design_document, relocate_design
from forge.verification.auto import _Generated, subsystem_dir

def _block_of(endpoint: str) -> str:
    return re.split(r"[.\[]", str(endpoint), maxsplit=1)[0]


def subsystem_design(project: Any, instances: Sequence[str]) -> Dict[str, Any]:
    """The project's design reduced to *instances*: their blocks, the
    connections among them, and every port a connection to the rest of the
    design reached made a port of the subsystem's top."""
    gen = _Generated(project, {})
    members = set(instances)
    doc = dict(load_design_document(project.design))
    blocks = {gen.instances[i] for i in members}
    split = sorted(b for b in blocks if any(blk == b and i not in members for i, blk in gen.instances.items()))
    if split:
        raise ValueError(f"a subsystem takes every instance of a block; {', '.join(split)} has instances "
                         "outside it (name the block instead)")
    if doc.get("topology_groups"):
        raise ValueError("a subsystem of a design with topology_groups cannot be cut from its connections yet")
    cut: Dict[str, Dict[str, set]] = {b: {"external_in_ports": set(), "external_out_ports": set()} for b in blocks}
    for c in gen.ir.get("connections", []):
        producer, consumer = c["producer"]["instance_id"], c["consumer"]["instance_id"]
        if producer not in gen.instances or consumer not in gen.instances or (producer in members) == (consumer in members):
            continue
        if consumer in members:
            cut[gen.instances[consumer]]["external_in_ports"].add(c["consumer"]["port"])
        else:
            cut[gen.instances[producer]]["external_out_ports"].add(c["producer"]["port"])
    doc = relocate_design(doc, Path(project.design).parent)
    modules = []
    for entry in doc.get("modules") or []:
        if entry.get("name") not in blocks:
            continue
        entry = dict(entry)
        for key, ports in cut[entry["name"]].items():
            merged = sorted(set(entry.get(key) or []) | ports)
            if merged:
                entry[key] = merged
        modules.append(entry)
    doc["modules"] = modules
    kept = []
    for conn in doc.get("connections") or []:
        if _block_of(conn["from"]) not in blocks:
            continue
        to = [t for t in (conn["to"] if isinstance(conn["to"], list) else [conn["to"]]) if _block_of(t) in blocks]
        if to:
            kept.append({**conn, "to": to if isinstance(conn["to"], list) else to[0]})
    doc["connections"] = kept
    return doc


def subsystem_difference(project: Any, name: str, instances: Sequence[str]) -> List[str]:
    """Connections among *instances* the generated subsystem *name* does not
    have exactly as the design does, either way; empty when it is faithful."""
    def wires(ir: Mapping[str, Any], keep: Any) -> set:
        return {(c["producer"]["instance_id"], c["producer"]["port"], c["consumer"]["instance_id"], c["consumer"]["port"])
                for c in ir.get("connections", [])
                if keep(c["producer"]["instance_id"]) and keep(c["consumer"]["instance_id"])}

    members = set(instances)
    full = wires(_Generated(project, {}).ir, lambda i: i in members)
    sub_ir = json.loads((subsystem_dir(project, name) / "design.ir.json").read_text())["design"]
    sub = wires(sub_ir, lambda i: i in members)
    return ([f"{a}.{b} -> {c}.{d} is missing" for a, b, c, d in sorted(full - sub)]
            + [f"{a}.{b} -> {c}.{d} is not in the design" for a, b, c, d in sorted(sub - full)])


def subsystems(project: Any, spec: Mapping[str, Any], select: Optional[Sequence[str]] = None) -> Dict[str, List[str]]:
    """target name -> instances, for every (selected) subsystem target."""
    out: Dict[str, List[str]] = {}
    targets = spec.get("targets") or {}
    if not isinstance(targets, Mapping) or (spec.get("target") or "generated") != "generated":
        return out
    gen = None
    for name, t in targets.items():
        if (t or {}).get("scope") != "subsystem" or (select and name not in select):
            continue
        gen = gen or _Generated(project, spec)
        out[str(name)] = gen.subsystem_instances((t or {}).get("instances") or [])
    return out
