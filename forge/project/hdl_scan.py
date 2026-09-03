"""Read every design unit out of an HDL file, not just the first one.

``forge.core.utils.hdl_parser`` answers the question a module registry asks:
"this entry names one module — what are its ports?". Adoption asks the
opposite question: "here is a repository nobody described — what is in it?".
The two differ in three ways that matter, and this module covers exactly
those three:

* **Every unit in the file.** A foreign repository routinely puts several
  modules in one file; ``hdl_parser`` stops at the first.
What counts as a clock or reset name is not decided here: that convention
lives in :mod:`forge.core.utils.signal_names`, shared with the structural
generators. Discovery and generation disagreeing about it is how a
peripheral's clock ends up tied to ground in a design discovery reported as
healthy.

* **The instantiation graph.** Which module instantiates which is what
  identifies top-level candidates and the pipeline order, and nothing in
  FORGE extracted it before.
* **What could not be read.** A unit whose ports came out empty, or that
  drives several clocks, is reported as such rather than dropped — the plan
  requires unsupported constructs to be detected during adoption, not after
  the user has written contracts for them.

Port parsing itself is delegated to ``hdl_parser``'s text-level scanners, so
adoption and the module registry can never disagree about a port's width.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from forge.core.utils.hdl_parser import (
    _scan_ports_text,
    _scan_verilog_ports_text,
    _strip_sv_comments,
)
from forge.core.utils.signal_names import (
    is_active_low_reset,
    is_clock_name,
    is_reset_name,
)

#: Verilog keywords that can open a statement looking exactly like an
#: instantiation (``<identifier> <identifier> (``). The instantiation graph
#: is additionally intersected with the set of modules actually discovered,
#: so this list only has to be good enough to keep the candidate set small.
_VLOG_STATEMENT_KEYWORDS = frozenset({
    "always", "always_comb", "always_ff", "always_latch", "assign", "begin",
    "case", "casex", "casez", "defparam", "else", "end", "endcase",
    "endfunction", "endgenerate", "endmodule", "endtask", "for", "foreach",
    "function", "generate", "if", "initial", "input", "inout", "integer",
    "localparam", "logic", "module", "output", "parameter", "reg", "repeat",
    "return", "task", "typedef", "while", "wire", "genvar", "assert",
    "assume", "cover", "property", "sequence", "import", "package",
})

_VLOG_MODULE_START_RE = re.compile(r"\bmodule\s+(?P<name>\w+)", re.M)
_VLOG_ENDMODULE_RE = re.compile(r"\bendmodule\b")

# `<module> [#(...)] <instance> (` — the shape of a Verilog instantiation.
_VLOG_INSTANCE_RE = re.compile(
    r"^\s*(?P<module>\w+)\s*(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
    r"(?P<instance>\w+)\s*\(",
    re.M,
)

_VHDL_ENTITY_RE = re.compile(r"^\s*entity\s+(?P<name>\w+)\s+is\b", re.I | re.M)
_VHDL_ARCH_RE = re.compile(
    r"^\s*architecture\s+(?P<arch>\w+)\s+of\s+(?P<entity>\w+)\s+is\b", re.I | re.M
)
# `label : [entity] [work.]name [generic|port] map` and bare component decls.
_VHDL_INSTANCE_RE = re.compile(
    r"^\s*\w+\s*:\s*(?:entity\s+)?(?:\w+\.)?(?P<module>\w+)\s*"
    r"(?:generic\s+map|port\s+map)",
    re.I | re.M,
)
_VHDL_COMPONENT_RE = re.compile(r"^\s*component\s+(?P<module>\w+)\b", re.I | re.M)




@dataclass(frozen=True)
class Port:
    """One port of a design unit, as its own source declares it."""

    name: str
    direction: str  # "in" | "out" | "inout"
    width: int

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "direction": self.direction, "width": self.width}


@dataclass
class HdlUnit:
    """One Verilog module or VHDL entity found in a source file.

    Attributes:
        name: Module/entity name as declared.
        path: File it was declared in.
        language: ``"verilog"``, ``"systemverilog"`` or ``"vhdl"``.
        ports: Declared ports, by name.
        instantiates: Names of the units this one instantiates, in source
            order, **once per instance** — a module instantiated four times
            appears four times. Filtered against the project's real unit
            names by :func:`resolve_instantiation_graph`; before that filter
            it is a candidate list and may contain false positives.
        unsupported: Human-readable reasons this unit cannot currently be
            managed by FORGE (see :meth:`clock_ports`).
    """

    name: str
    path: Path
    language: str
    ports: Dict[str, Port] = field(default_factory=dict)
    instantiates: Tuple[str, ...] = ()
    unsupported: Tuple[str, ...] = ()
    #: The user declared this module opaque: integrate it structurally,
    #: do not model its internals. Its :attr:`unsupported` reasons are then
    #: recorded as *why it is opaque* rather than as a blocker.
    opaque: bool = False

    # ── Derived views ─────────────────────────────────────────────────────

    @property
    def clock_ports(self) -> List[str]:
        """Input ports whose names follow a clock convention."""
        return [
            name for name, port in self.ports.items()
            if port.direction == "in" and port.width == 1 and is_clock_name(name)
        ]

    @property
    def reset_ports(self) -> List[str]:
        """Input ports whose names follow a reset convention."""
        return [
            name for name, port in self.ports.items()
            if port.direction == "in" and port.width == 1 and is_reset_name(name)
        ]

    def unmanaged_clock_ports(self, primary_clock: str) -> List[str]:
        """Clock inputs FORGE will not drive from the design's global net.

        For an opaque module with several functional clocks: every one that
        is not the design's own clock has to reach the top level for the
        enclosing design to drive, because tying them all to ``ap_clk``
        would short distinct domains together. Returns them in sorted order
        so generated configuration stays byte-stable.
        """
        return sorted(name for name in self.clock_ports if name != primary_clock)

    def unmanaged_reset_ports(self, primary_reset: str) -> List[str]:
        """The same, for resets."""
        return sorted(name for name in self.reset_ports if name != primary_reset)

    @property
    def data_ports(self) -> List[str]:
        """Everything that is neither clock nor reset."""
        infra = set(self.clock_ports) | set(self.reset_ports)
        return [name for name in self.ports if name not in infra]

    def inputs(self) -> List[Port]:
        return [p for n, p in self.ports.items() if p.direction == "in" and n in self.data_ports]

    def outputs(self) -> List[Port]:
        return [p for n, p in self.ports.items() if p.direction == "out" and n in self.data_ports]

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "language": self.language,
            "ports": [p.to_dict() for p in self.ports.values()],
            "clock_ports": self.clock_ports,
            "reset_ports": self.reset_ports,
            "instantiates": list(self.instantiates),
            "unsupported": list(self.unsupported),
            "opaque": self.opaque,
        }


#: Re-exported so callers of this module do not need to know that the
#: convention lives in ``forge.core``. It is shared with the structural
#: generators, which is the point — discovery and generation disagreeing
#: about what a clock is called is how a peripheral's clock ends up tied to
#: ground in a design discovery said was fine.
is_active_low = is_active_low_reset


# ── Verilog ───────────────────────────────────────────────────────────────

def split_verilog_modules(text: str) -> List[Tuple[str, str]]:
    """``[(name, source_slice)]`` for every module in *text*.

    Slices run from the ``module`` keyword to its matching ``endmodule`` so
    each slice is independently parseable by ``hdl_parser``'s text scanner.
    A module missing its ``endmodule`` (a truncated or generated file) runs
    to the start of the next module rather than swallowing the rest of the
    file.
    """
    stripped = _strip_sv_comments(text)
    starts = [(m.start(), m.group("name")) for m in _VLOG_MODULE_START_RE.finditer(stripped)]
    units: List[Tuple[str, str]] = []
    for index, (start, name) in enumerate(starts):
        next_start = starts[index + 1][0] if index + 1 < len(starts) else len(stripped)
        end_match = _VLOG_ENDMODULE_RE.search(stripped, start, next_start)
        end = end_match.end() if end_match else next_start
        units.append((name, stripped[start:end]))
    return units


def _verilog_instantiations(module_source: str, module_name: str) -> Tuple[str, ...]:
    """Candidate module names instantiated inside one module's source.

    One entry per *instantiation*, so a module instantiated four times is
    listed four times. How many instances a design contains is a fact its
    own top level proves, and adoption writes it into ``instances:`` — a
    list of distinct names could not support that.

    Deduplicated by instance label rather than by module name, since the
    label is what makes two instantiations of the same module distinct.
    """
    found: List[str] = []
    seen_labels = set()
    for match in _VLOG_INSTANCE_RE.finditer(module_source):
        candidate = match.group("module")
        if candidate in _VLOG_STATEMENT_KEYWORDS or candidate == module_name:
            continue
        # `module foo (` itself matches the instantiation shape.
        if module_source[: match.start()].rstrip().endswith("module"):
            continue
        label = (candidate, match.group("instance"))
        if label in seen_labels:
            continue
        seen_labels.add(label)
        found.append(candidate)
    return tuple(found)


def scan_verilog_file(path: Path) -> List[HdlUnit]:
    """Every module declared in a Verilog/SystemVerilog file."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    language = "systemverilog" if path.suffix.lower() in (".sv", ".svh") else "verilog"
    units: List[HdlUnit] = []
    for name, source in split_verilog_modules(text):
        ports = {
            port_name: Port(port_name, direction, width)
            for port_name, (direction, width) in _scan_verilog_ports_text(source).items()
        }
        units.append(
            HdlUnit(
                name=name,
                path=path,
                language=language,
                ports=ports,
                instantiates=_verilog_instantiations(source, name),
                unsupported=_unsupported_reasons(name, ports),
            )
        )
    return units


# ── VHDL ──────────────────────────────────────────────────────────────────

def split_vhdl_entities(text: str) -> List[Tuple[str, str]]:
    """``[(name, source_slice)]`` for every entity in *text*.

    A slice runs from ``entity X is`` to whichever comes first: the next
    entity, or the architecture that implements it — the port clause is
    inside the entity declaration, so nothing after that boundary is needed
    for port scanning.
    """
    boundaries = sorted(
        [(m.start(), m.group("name")) for m in _VHDL_ENTITY_RE.finditer(text)]
        + [(m.start(), None) for m in _VHDL_ARCH_RE.finditer(text)]
    )
    units: List[Tuple[str, str]] = []
    for index, (start, name) in enumerate(boundaries):
        if name is None:
            continue
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        units.append((name, text[start:end]))
    return units


def _vhdl_instantiations(text: str, entity_name: str) -> Tuple[str, ...]:
    """Candidate entity names instantiated in the architecture of *entity_name*."""
    architectures = [
        m for m in _VHDL_ARCH_RE.finditer(text)
        if m.group("entity").lower() == entity_name.lower()
    ]
    found: List[str] = []
    for arch in architectures:
        next_arch = _VHDL_ARCH_RE.search(text, arch.end())
        body = text[arch.start(): next_arch.start() if next_arch else len(text)]
        # Instantiations are counted; a `component` declaration is not an
        # instance, so it only registers a name that has none yet.
        for match in _VHDL_INSTANCE_RE.finditer(body):
            candidate = match.group("module")
            if candidate.lower() != entity_name.lower():
                found.append(candidate)
        for match in _VHDL_COMPONENT_RE.finditer(body):
            candidate = match.group("module")
            if candidate.lower() != entity_name.lower() and candidate not in found:
                found.append(candidate)
    return tuple(found)


def scan_vhdl_file(path: Path) -> List[HdlUnit]:
    """Every entity declared in a VHDL file."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    units: List[HdlUnit] = []
    for name, source in split_vhdl_entities(text):
        ports = {
            port_name: Port(port_name, direction, width)
            for port_name, (direction, width) in _scan_ports_text(source).items()
        }
        units.append(
            HdlUnit(
                name=name,
                path=path,
                language="vhdl",
                ports=ports,
                instantiates=_vhdl_instantiations(text, name),
                unsupported=_unsupported_reasons(name, ports),
            )
        )
    return units


def scan_hdl_file(path: Path) -> List[HdlUnit]:
    """Dispatch on suffix. Unknown suffixes yield nothing."""
    suffix = path.suffix.lower()
    if suffix in (".v", ".sv", ".vh", ".svh"):
        return scan_verilog_file(path)
    if suffix in (".vhd", ".vhdl"):
        return scan_vhdl_file(path)
    return []


# ── Limits detected at scan time ──────────────────────────────────────────

def _unsupported_reasons(name: str, ports: Dict[str, Port]) -> Tuple[str, ...]:
    """FORGE limitations this unit runs into, detected from its ports alone.

    Reported here, during discovery, so a user learns that (say) their
    Ethernet core has three functional clocks *before* writing a contract
    for it — the plan's Phase D3 requirement. Both checks are about FORGE's
    documented scope limits, not about the module being wrong.
    """
    reasons: List[str] = []
    unit = HdlUnit(name=name, path=Path("."), language="", ports=ports)
    clocks = unit.clock_ports
    if len(clocks) > 1:
        reasons.append(
            f"{len(clocks)} clock inputs ({', '.join(sorted(clocks))}) — FORGE "
            f"manages one functional clock domain per module"
        )
    if not ports:
        reasons.append(
            "no ports could be read from the source — the declaration may use "
            "constructs this scanner does not cover"
        )
    return tuple(reasons)


def resolve_instantiation_graph(units: List[HdlUnit]) -> Dict[str, Tuple[str, ...]]:
    """Restrict each unit's candidate instantiations to real units.

    The per-file regexes over-collect by design (a task call and an
    instantiation look alike). Intersecting against the names actually
    discovered removes every false positive that matters, because a name
    that is not a module in this project cannot be an instance of one.

    Multiplicity is preserved: a module instantiated four times appears four
    times in its parent's tuple. Callers that want distinct names should say
    so with ``set()`` — losing the counts here would silently discard the
    only evidence a design has for how many instances it contains.
    """
    known = {unit.name for unit in units}
    return {
        unit.name: tuple(dep for dep in unit.instantiates if dep in known and dep != unit.name)
        for unit in units
    }


def instance_counts(units: List[HdlUnit], parent: str) -> Dict[str, int]:
    """How many times *parent* instantiates each module.

    The one fact that lets adoption write a real ``instances:`` count rather
    than defaulting every module to 1 — and it is genuinely *proven* by the
    parent's own source, not inferred, so it is safe to write.
    """
    graph = resolve_instantiation_graph(units)
    counts: Dict[str, int] = {}
    for dependency in graph.get(parent, ()):
        counts[dependency] = counts.get(dependency, 0) + 1
    return counts


def top_level_candidates(units: List[HdlUnit]) -> List[str]:
    """Units nobody instantiates.

    A design's top level is the unit no other unit instantiates. Several
    such units is the normal case in a real repository (the top, plus every
    testbench and every unused block), so this returns all of them and lets
    the caller rank — it is a candidate list, not an answer.
    """
    graph = resolve_instantiation_graph(units)
    instantiated = {dep for deps in graph.values() for dep in deps}
    return [unit.name for unit in units if unit.name not in instantiated]


def distinct_dependencies(graph: Dict[str, Tuple[str, ...]], name: str) -> List[str]:
    """The distinct modules *name* instantiates, in first-seen order.

    For every caller that wants "what does this depend on?" rather than "how
    many of each?" — display, ranking, cycle checks.
    """
    seen: List[str] = []
    for dependency in graph.get(name, ()):
        if dependency not in seen:
            seen.append(dependency)
    return seen
