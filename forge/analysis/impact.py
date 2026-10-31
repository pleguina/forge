"""What a change to a design affects: compared semantically, not by hash.

:func:`compare` takes two resolved designs (the ``design`` mapping of an IR
snapshot) and reports, by stable name, what changed in each module (ports,
parameters, latency, kind, sources), instance (its module, clock and reset
domain) and connection (its endpoints' widths, its pipeline registers and
delays), which instances that makes behave or connect differently, what lies
downstream of them, and every merge point whose inputs are no longer aligned
the way they were: one branch into it changed timing and another did not.

Facts only knowable after generation (top-level ports, tie-offs) are left
out of both sides, so a built snapshot compares cleanly with a design that
has only been resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

#: Transformation kinds that add cycles to the connection they sit on.
TIMING_KINDS = frozenset({"pipeline_register", "latency_delay", "slr_crossing", "cdc_synchronizer",
                          "pulse_sync", "mailbox_transfer", "async_fifo"})


@dataclass
class Impact:
    """The result of :func:`compare`; every list is sorted, every name stable."""

    modules: Dict[str, List[str]] = field(default_factory=dict)
    instances_added: List[str] = field(default_factory=list)
    instances_removed: List[str] = field(default_factory=list)
    instances_changed: Dict[str, List[str]] = field(default_factory=dict)
    connections_added: List[str] = field(default_factory=list)
    connections_removed: List[str] = field(default_factory=list)
    connections_changed: Dict[str, List[str]] = field(default_factory=dict)
    sources_changed: Dict[str, List[str]] = field(default_factory=dict)   # module -> files
    affected: List[str] = field(default_factory=list)
    downstream: List[str] = field(default_factory=list)
    merges: List[Dict[str, Any]] = field(default_factory=list)
    sources: Dict[str, List[str]] = field(default_factory=dict)          # module -> its files

    @property
    def empty(self) -> bool:
        return not (self.modules or self.instances_added or self.instances_removed or self.instances_changed
                    or self.connections_added or self.connections_removed or self.connections_changed
                    or self.sources_changed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modules": self.modules,
            "sources_changed": self.sources_changed,
            "instances": {"added": self.instances_added, "removed": self.instances_removed,
                          "changed": self.instances_changed},
            "connections": {"added": self.connections_added, "removed": self.connections_removed,
                            "changed": self.connections_changed},
            "affected": self.affected,
            "downstream": self.downstream,
            "merges": self.merges,
        }


def _real(connections: Iterable[Mapping[str, Any]], instances: Set[str]) -> Dict[str, Mapping[str, Any]]:
    return {c["id"]: c for c in connections
            if c["producer"]["instance_id"] in instances and c["consumer"]["instance_id"] in instances}


def _ports(module: Mapping[str, Any]) -> Dict[str, Tuple[str, int]]:
    return {p["name"]: (p["direction"], int(p["width"])) for p in module.get("ports") or []}


def _latency(module: Mapping[str, Any]) -> Any:
    return module.get("latency") or module.get("latency_cycles")


def _say_latency(latency: Any) -> str:
    if latency is None:
        return "undeclared"
    if not isinstance(latency, Mapping):
        return f"{latency} cycles"
    if latency.get("cycles") is not None:
        return f"{latency['cycles']} cycles"
    if latency.get("min_cycles") is not None or latency.get("max_cycles") is not None:
        return f"{latency.get('min_cycles', '?')}..{latency.get('max_cycles', '?')} cycles"
    return str(latency.get("kind") or latency)


def _module_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    old, new = _ports(before), _ports(after)
    for name in sorted(set(old) | set(new)):
        if name not in new:
            out.append(f"port {name} removed")
        elif name not in old:
            out.append(f"port {name} added ({new[name][0]}, {new[name][1]} bit)")
        elif old[name] != new[name]:
            (d0, w0), (d1, w1) = old[name], new[name]
            out.append(f"port {name}: " + (f"{w0} -> {w1} bit" if d0 == d1 else f"{d0} -> {d1}"))
    p0, p1 = before.get("parameters") or {}, after.get("parameters") or {}
    for name in sorted(set(p0) | set(p1)):
        if p0.get(name) != p1.get(name):
            out.append(f"parameter {name}: {p0.get(name, 'unset')} -> {p1.get(name, 'unset')}")
    if _latency(before) != _latency(after):
        out.append(f"latency: {_say_latency(_latency(before))} -> {_say_latency(_latency(after))}")
    for key in ("kind", "top", "language"):
        if before.get(key) != after.get(key):
            out.append(f"{key}: {before.get(key)} -> {after.get(key)}")
    i0 = {i.get("name"): i.get("protocol") for i in before.get("interfaces") or []}
    i1 = {i.get("name"): i.get("protocol") for i in after.get("interfaces") or []}
    for name in sorted(set(i0) | set(i1)):
        if i0.get(name) != i1.get(name):
            out.append(f"interface {name} protocol: {i0.get(name)} -> {i1.get(name)}")
    if sorted(before.get("source_files") or []) != sorted(after.get("source_files") or []):
        out.append("source files: " + ", ".join(after.get("source_files") or []) + " (were "
                   + ", ".join(before.get("source_files") or []) + ")")
    return out


def _timing(connection: Mapping[str, Any]) -> List[Tuple[str, int, str]]:
    return sorted((str(t["kind"]), int(t.get("cycles") or 0), str(t.get("tag") or ""))
                  for t in connection.get("transformations") or [] if t.get("kind") in TIMING_KINDS)


def _say_timing(connection: Mapping[str, Any]) -> str:
    return ", ".join(kind + (f" {cycles} cycle(s)" if cycles else "") for kind, cycles, _ in _timing(connection)) or "none"


def _connection_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    e0, e1 = before.get("matching_evidence") or {}, after.get("matching_evidence") or {}
    for side in ("producer", "consumer"):
        w0, w1 = e0.get(f"{side}_width"), e1.get(f"{side}_width")
        if w0 != w1:
            out.append(f"{side} width {w0} -> {w1}")
    if _timing(before) != _timing(after):
        out.append(f"timing: {_say_timing(before)} -> {_say_timing(after)}")
    if before.get("wiring_method") != after.get("wiring_method"):
        out.append(f"wiring: {before.get('wiring_method')} -> {after.get('wiring_method')}")
    if before.get("crosses_clock_domain") != after.get("crosses_clock_domain"):
        out.append("now crosses a clock domain" if after.get("crosses_clock_domain") else "no longer crosses a clock domain")
    return out


def compare(before: Mapping[str, Any], after: Mapping[str, Any],
            changed_files: Sequence[str] = ()) -> Impact:
    """The :class:`Impact` of going from design *before* to *after*.

    *changed_files* are source files whose content changed while their names
    did not (from a build's provenance); the modules built from them are
    reported as changed in behaviour."""
    impact = Impact()
    mods0 = {m["name"]: m for m in before.get("modules") or []}
    mods1 = {m["name"]: m for m in after.get("modules") or []}
    impact.sources = {name: list(m.get("rtl_sources") or m.get("source_files") or []) for name, m in mods1.items()}
    for name in sorted(set(mods0) | set(mods1)):
        if name not in mods1:
            impact.modules[name] = ["module removed"]
        elif name not in mods0:
            impact.modules[name] = ["module added"]
        else:
            changes = _module_changes(mods0[name], mods1[name])
            if changes:
                impact.modules[name] = changes
    changed = {str(f) for f in changed_files}
    for name, files in impact.sources.items():
        hit = sorted(f for f in files if f in changed)
        if hit:
            impact.sources_changed[name] = hit

    inst0 = {i["id"]: i for i in before.get("instances") or []}
    inst1 = {i["id"]: i for i in after.get("instances") or []}
    impact.instances_added = sorted(set(inst1) - set(inst0))
    impact.instances_removed = sorted(set(inst0) - set(inst1))
    for iid in sorted(set(inst0) & set(inst1)):
        a, b = inst0[iid], inst1[iid]
        diffs = [f"{key.replace('_', ' ')}: {a.get(key)} -> {b.get(key)}"
                 for key in ("module", "clock_domain", "reset_domain") if a.get(key) != b.get(key)]
        if diffs:
            impact.instances_changed[iid] = diffs

    conn0 = _real(before.get("connections") or [], set(inst0))
    conn1 = _real(after.get("connections") or [], set(inst1))
    impact.connections_added = sorted(set(conn1) - set(conn0))
    impact.connections_removed = sorted(set(conn0) - set(conn1))
    for cid in sorted(set(conn0) & set(conn1)):
        diffs = _connection_changes(conn0[cid], conn1[cid])
        if diffs:
            impact.connections_changed[cid] = diffs

    def module_of(iid: str) -> Optional[str]:
        return (inst1.get(iid) or inst0.get(iid) or {}).get("module")

    changed_modules = set(impact.modules) | set(impact.sources_changed)
    affected = {i for i in inst1 if module_of(i) in changed_modules}
    affected |= set(impact.instances_added) | set(impact.instances_changed)
    for cid in [*impact.connections_added, *impact.connections_changed]:
        c = conn1[cid]
        affected |= {c["producer"]["instance_id"], c["consumer"]["instance_id"]}
    for cid in impact.connections_removed:
        affected |= {e for e in (conn0[cid]["producer"]["instance_id"], conn0[cid]["consumer"]["instance_id"])
                     if e in inst1}
    impact.affected = sorted(affected)

    consumers: Dict[str, Set[str]] = {}
    for c in conn1.values():
        consumers.setdefault(c["producer"]["instance_id"], set()).add(c["consumer"]["instance_id"])
    reach: Set[str] = set()
    todo = list(affected)
    while todo:
        for nxt in consumers.get(todo.pop(), ()):
            if nxt not in reach and nxt not in affected:
                reach.add(nxt)
                todo.append(nxt)
    impact.downstream = sorted(reach)
    impact.merges = _misaligned_merges(impact, conn0, conn1, inst1, mods0, mods1)
    return impact


def _misaligned_merges(impact: Impact, conn0: Mapping[str, Mapping[str, Any]], conn1: Mapping[str, Mapping[str, Any]],
                       inst1: Mapping[str, Mapping[str, Any]], mods0: Mapping[str, Any],
                       mods1: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Instances fed by several producers where the timing of some branches
    changed and of others did not."""
    retimed = {i for i, inst in inst1.items()
               if inst["module"] in mods0 and inst["module"] in mods1
               and _latency(mods0[inst["module"]]) != _latency(mods1[inst["module"]])}
    retimed |= set(impact.instances_added) | {i for i, d in impact.instances_changed.items()
                                              if any(x.startswith("module") for x in d)}
    retimed_edges = {cid for cid in conn1 if cid not in conn0 or _timing(conn0[cid]) != _timing(conn1[cid])}
    into: Dict[str, List[Mapping[str, Any]]] = {}
    for c in conn1.values():
        into.setdefault(c["consumer"]["instance_id"], []).append(c)
    dirty = set(retimed)
    grew = True
    while grew:
        grew = False
        for node, edges in into.items():
            if node not in dirty and any(e["producer"]["instance_id"] in dirty or e["id"] in retimed_edges
                                         for e in edges):
                dirty.add(node)
                grew = True
    merges: List[Dict[str, Any]] = []
    for node, edges in sorted(into.items()):
        branches: Dict[str, bool] = {}
        for e in edges:
            producer = e["producer"]["instance_id"]
            branches[producer] = branches.get(producer, False) or producer in dirty or e["id"] in retimed_edges
        if len(branches) > 1 and any(branches.values()) and not all(branches.values()):
            merges.append({"instance": node,
                           "changed": sorted(p for p, d in branches.items() if d),
                           "unchanged": sorted(p for p, d in branches.items() if not d)})
    return merges


def affected_targets(impact: Impact, targets: Mapping[str, Mapping[str, Any]],
                     variant: Optional[str] = None) -> Dict[str, str]:
    """target name -> why it needs to run again, for targets given as
    ``{name: {scope, instances?, replay?, variant?}}`` (``forge test``'s plan
    levels). The change is to the project's design, which every variant is
    made from, or with *variant* to that variant alone."""
    if impact.empty:
        return {}
    changed_modules = set(impact.modules) | set(impact.sources_changed)
    affected = set(impact.affected)
    out: Dict[str, str] = {}
    for name, t in targets.items():
        scope = str(t.get("scope") or "design")
        if variant is not None:
            if t.get("variant") == variant:
                out[name] = f"variant {variant} changed"
        elif scope == "design":
            out[name] = "the design changed"
        elif scope.startswith("module:") and scope[7:] in changed_modules:
            out[name] = f"module {scope[7:]} changed"
        elif scope.startswith("instance:") and scope[9:] in affected:
            out[name] = f"instance {scope[9:]} changed"
        elif scope == "subsystem":
            hit = sorted(set(t.get("instances") or []) & affected)
            if hit:
                out[name] = f"its instances {', '.join(hit)} changed"
    for name, t in targets.items():
        source = t.get("replay")
        if name not in out and source and source in out:
            out[name] = f"the traffic {source} records will change once it runs again"
    return out


def render_markdown(impact: Impact, targets: Mapping[str, str], artifacts: Sequence[str],
                    base: str) -> str:
    """The impact report ``forge inspect --impact`` writes."""
    lines = ["# Change impact", "", f"Compared with {base}.", ""]
    if impact.empty:
        return "\n".join(lines + ["Nothing changed.", ""])

    def section(title: str, rows: Mapping[str, Sequence[str]], where: Optional[Mapping[str, Sequence[str]]] = None) -> None:
        if not rows:
            return
        lines.extend([f"## {title}", ""])
        for name, changes in rows.items():
            files = f" ({', '.join(where[name])})" if where and where.get(name) else ""
            lines.append(f"- **{name}**{files}: " + "; ".join(changes))
        lines.append("")

    section("Modules", impact.modules, impact.sources)
    section("Sources changed since the build", impact.sources_changed)
    section("Instances", {**{i: ["added"] for i in impact.instances_added},
                          **{i: ["removed"] for i in impact.instances_removed}, **impact.instances_changed})
    section("Connections", {**{c: ["added"] for c in impact.connections_added},
                            **{c: ["removed"] for c in impact.connections_removed}, **impact.connections_changed})
    lines += ["## What it affects", "",
              f"- Changed: {', '.join(impact.affected) or 'none'}",
              f"- Downstream of them: {', '.join(impact.downstream) or 'none'}", ""]
    if impact.merges:
        lines += ["## Merges whose inputs are no longer aligned", ""]
        for m in impact.merges:
            lines.append(f"- **{m['instance']}**: the branch from {', '.join(m['changed'])} changed timing, "
                         f"the one from {', '.join(m['unchanged'])} did not")
        lines.append("")
    if targets:
        lines += ["## Verification to run again", ""] + [f"- `{n}`: {why}" for n, why in targets.items()] + [""]
    if artifacts:
        lines += ["## To regenerate", ""] + [f"- {a}" for a in artifacts] + [""]
    return "\n".join(lines)
