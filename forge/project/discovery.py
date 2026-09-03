"""Find out what is in a repository FORGE has never seen.

This is the first half of ``forge adopt``: walk an existing project, read
its sources, and produce a structured account of what is there — modules and
their ports, which module instantiates which, the clocks and resets, the
likely top levels, the HLS kernels, the testbenches, the constraints — plus,
just as importantly, an explicit list of what could *not* be determined.

The rule the plan states and this module enforces throughout: a conclusion is
either **known** (the source proves it), **inferred** (a convention FORGE
recognises applied unambiguously), **ambiguous** (several readings are
equally valid, so FORGE stops and asks) or **unsupported** (FORGE's current
scope cannot express it). Nothing ambiguous is ever quietly promoted to
known. Every conclusion carries :class:`~forge.project.evidence.Evidence`
so ``forge check`` can explain it and the user can disagree with it.

Discovery reads. It never writes, never creates a directory, and never
requires the repository to be laid out any particular way — that is the
whole point of it existing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from forge.project.actions import Action
from forge.project.config import ProjectDecisions
from forge.project.paths import ROOT_CONFIG_NAME
from forge.project.evidence import (
    DETERMINISTIC,
    HEURISTIC,
    Evidence,
    evidence_list,
    weakest_confidence,
)
from forge.project.evidence import INFERRED as INFERRED_CONFIDENCE
from forge.project.hdl_scan import (
    HdlUnit,
    distinct_dependencies,
    instance_counts,
    is_active_low,
    resolve_instantiation_graph,
    scan_hdl_file,
)

# ── Classification of a discovered fact ───────────────────────────────────

#: The source states it outright.
KNOWN = "known"
#: A convention FORGE recognises resolved it, unambiguously. Spelled the
#: same as :data:`forge.project.evidence.INFERRED` (imported above as
#: ``INFERRED_CONFIDENCE``) but a different vocabulary: this classifies a
#: *fact*, that one grades a *conclusion's* trustworthiness.
INFERRED = "inferred"
#: More than one reading is equally valid. FORGE stops here and asks.
AMBIGUOUS = "ambiguous"
#: FORGE's current scope cannot express it.
UNSUPPORTED = "unsupported"

CLASSIFICATIONS = (KNOWN, INFERRED, AMBIGUOUS, UNSUPPORTED)

# ── File classification ───────────────────────────────────────────────────

_RTL_SUFFIXES = frozenset({".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"})
_HLS_SUFFIXES = frozenset({".cpp", ".cc", ".cxx"})
_HEADER_SUFFIXES = frozenset({".h", ".hpp", ".hh"})
_CONSTRAINT_SUFFIXES = frozenset({".xdc", ".sdc"})
_DATA_SUFFIXES = frozenset({".xml", ".csv", ".json", ".mem", ".dat"})

#: Directories never worth walking into: version control, build output,
#: virtualenvs, caches, and FORGE's own state directory (adopting an already
#: adopted project must not rediscover its own generated files as sources).
_IGNORED_DIRS = frozenset({
    ".git", ".svn", ".hg", ".forge", ".venv", "venv", "__pycache__", ".tox",
    "node_modules", ".mypy_cache", ".pytest_cache", "htmlcov", ".Xil",
    ".ipynb_checkpoints", "site", ".idea", ".vscode",
})

#: Directory names that mark their contents as simulation-only.
_TESTBENCH_DIRS = frozenset({"tb", "test", "tests", "testbench", "sim", "bench", "verify"})

#: Directory names that mark their contents as build output rather than
#: source — a generated top level found here must not be mistaken for one
#: of the user's own modules.
_BUILD_DIRS = frozenset({"build", "gen-top", "obj", "out", "dist", "work", "_build"})

_BUILD_FILENAMES = frozenset({
    "makefile", "cmakelists.txt", "meson.build", "wscript", "build.sh", "sources.f",
})

_TESTBENCH_NAME_RE = re.compile(r"(^tb[_.]|_tb$|^test_|_test$|_tb_)", re.I)

#: A C++ function definition at file scope — used only to find *candidate*
#: HLS top functions, so it is deliberately permissive.
_CPP_FUNC_RE = re.compile(
    r"^(?!\s*(?:if|for|while|switch|return|else)\b)"
    r"[\w:<>,\s\*&]+?\s+(?P<name>\w+)\s*\([^;{}]*\)\s*\{",
    re.M,
)
_HLS_PRAGMA_RE = re.compile(r"#pragma\s+HLS", re.I)


# ── Discovered things ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class DiscoveredFile:
    """One file, and what discovery believes it is for."""

    path: Path
    role: str  # "rtl" | "hls" | "header" | "testbench" | "constraint" | "build" | "data"
    language: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"path": str(self.path), "role": self.role, "language": self.language}


@dataclass
class SignalCandidate:
    """A clock or reset FORGE believes the design has.

    Attributes:
        name: The port name the convention matched on.
        ports: ``module.port`` for every place it appears — the proof.
        active_low: For resets only; ``None`` for clocks.
        evidence: Why FORGE concluded this.
    """

    name: str
    ports: Tuple[str, ...]
    active_low: Optional[bool] = None
    evidence: Tuple[Evidence, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ports": list(self.ports),
            "active_low": self.active_low,
            "evidence": evidence_list(self.evidence),
        }


@dataclass
class InferredConnection:
    """A producer/consumer pair discovery believes should be wired.

    ``confidence`` is the weakest link in ``evidence``, never stronger — so
    a connection resting on a name convention can never present itself as
    proven.
    """

    producer: str  # "module.port"
    consumer: str  # "module.port"
    width: int
    evidence: Tuple[Evidence, ...] = ()

    @property
    def confidence(self) -> str:
        return weakest_confidence(self.evidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "producer": self.producer,
            "consumer": self.consumer,
            "width": self.width,
            "confidence": self.confidence,
            "evidence": evidence_list(self.evidence),
        }


#: What an :class:`Ambiguity` is *about*. Each maps to the check that owns
#: it, so one question is reported once, by the section it belongs to,
#: rather than by whichever check happened to iterate over it first.
KIND_CONNECTION = "connection"
KIND_CLOCK = "clock"
KIND_TOP = "top"
KIND_HLS_TOP = "hls_top"
KIND_MODULE = "module"


@dataclass
class Ambiguity:
    """One decision FORGE refuses to make on the user's behalf.

    This is the plan's central rule made concrete: where more than one
    reading is valid, FORGE records the question, every option it
    considered, and the command that answers it — and writes nothing.
    """

    id: str
    classification: str
    subject: str
    question: str
    kind: str = KIND_CONNECTION
    options: Tuple[str, ...] = ()
    action: Optional[Action] = None
    evidence: Tuple[Evidence, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "classification": self.classification,
            "kind": self.kind,
            "subject": self.subject,
            "question": self.question,
            "options": list(self.options),
            "action": self.action.to_dict() if self.action else None,
            "evidence": evidence_list(self.evidence),
        }


@dataclass
class HlsCandidate:
    """A C++ source that looks like an HLS kernel."""

    path: Path
    top: str
    has_pragmas: bool
    evidence: Tuple[Evidence, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "top": self.top,
            "has_pragmas": self.has_pragmas,
            "evidence": evidence_list(self.evidence),
        }


@dataclass
class DiscoveryResult:
    """Everything one scan of a repository found.

    Deliberately a plain data record: ``forge adopt`` turns it into project
    files, ``forge check`` re-derives project health from it, and
    ``forge next`` reads its ambiguity list. None of them re-walk the tree.
    """

    root: Path
    files: List[DiscoveredFile] = field(default_factory=list)
    units: List[HdlUnit] = field(default_factory=list)
    testbench_units: List[HdlUnit] = field(default_factory=list)
    instantiation_graph: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    top_candidates: List[str] = field(default_factory=list)
    clocks: List[SignalCandidate] = field(default_factory=list)
    resets: List[SignalCandidate] = field(default_factory=list)
    hls_candidates: List[HlsCandidate] = field(default_factory=list)
    connections: List[InferredConnection] = field(default_factory=list)
    ambiguities: List[Ambiguity] = field(default_factory=list)
    unsupported: List[Ambiguity] = field(default_factory=list)
    external_inputs: List[str] = field(default_factory=list)
    external_outputs: List[str] = field(default_factory=list)

    # ── Views ─────────────────────────────────────────────────────────────

    def files_by_role(self, role: str) -> List[DiscoveredFile]:
        return [f for f in self.files if f.role == role]

    def unit(self, name: str) -> Optional[HdlUnit]:
        return next((u for u in self.units if u.name == name), None)

    @property
    def structural_top(self) -> Optional[str]:
        """The user's existing top level, if the repository has one.

        Only a unit that both instantiates other discovered units and is
        instantiated by none of them qualifies. A single-module project has
        no structural top — its one module is a block to integrate, not a
        top level to replace — and this returns ``None`` there.
        """
        for name in self.top_candidates:
            if self.instantiation_graph.get(name):
                return name
        return None

    @property
    def instance_counts(self) -> Dict[str, int]:
        """How many instances of each managed module the design contains.

        Taken from the existing structural top's own instantiations, which
        *prove* the count. A design with no structural top has nothing to
        prove a count with, so every module is one instance — the honest
        answer, and the one adoption writes.
        """
        top = self.structural_top
        if top is None:
            return {unit.name: 1 for unit in self.managed_units}
        counts = instance_counts(self.units, top)
        return {unit.name: counts.get(unit.name, 1) for unit in self.managed_units}

    @property
    def opaque_units(self) -> List[HdlUnit]:
        """Modules integrated structurally without being modelled inside."""
        return [u for u in self.managed_units if u.opaque]

    @property
    def managed_units(self) -> List[HdlUnit]:
        """Units FORGE would integrate: leaf blocks, not the top, not a TB.

        An existing structural top is excluded because FORGE *generates* the
        structural top; adopting the user's as a managed module would ask
        FORGE to instantiate the very thing it is replacing. A module whose
        source hit a FORGE limitation is excluded too — it is reported as
        unsupported instead of silently half-integrated.
        """
        top = self.structural_top
        return [
            u for u in self.units
            if u.name != top and (u.opaque or not u.unsupported)
        ]

    @property
    def blocking_ambiguities(self) -> List[Ambiguity]:
        return [a for a in self.ambiguities if a.classification == AMBIGUOUS]

    def _on_managed(self, candidates: List[SignalCandidate]) -> List[SignalCandidate]:
        """Filter to signals reaching a module FORGE both integrates *and*
        models internally.

        Opaque modules are deliberately excluded even though they are
        managed: their extra clock and reset pins are exposed at the top
        level for the enclosing design to drive, so they are by definition
        not the design's own functional clock. Including them would have
        FORGE ask which of a vendor IP's three internal clocks runs a design
        that does not drive any of them.
        """
        modelled = {unit.name for unit in self.managed_units if not unit.opaque}
        return [
            candidate for candidate in candidates
            if any(port.split(".")[0] in modelled for port in candidate.ports)
        ]

    @property
    def managed_clocks(self) -> List[SignalCandidate]:
        """Clocks that could be *the* functional clock of this design.

        A vendor IP — whether excluded as unsupported or integrated as
        opaque — still contributes its clocks to :attr:`clocks`, because
        they are real and worth reporting. They are not candidates here.
        """
        return self._on_managed(self.clocks)

    @property
    def managed_resets(self) -> List[SignalCandidate]:
        """Resets that reach a module FORGE actually manages."""
        return self._on_managed(self.resets)

    def ambiguities_of(self, *kinds: str) -> List[Ambiguity]:
        """Only the questions about *kinds*.

        Each check reports the ambiguities it owns, so a multi-clock
        repository raises its clock question once — under Clock/reset — and
        not a second time under Topology.
        """
        return [a for a in self.ambiguities if a.kind in kinds]

    def counts(self) -> Dict[str, int]:
        return {
            "files": len(self.files),
            "rtl_files": len(self.files_by_role("rtl")),
            "hls_files": len(self.files_by_role("hls")),
            "testbench_files": len(self.files_by_role("testbench")),
            "constraint_files": len(self.files_by_role("constraint")),
            "modules": len(self.units),
            "hls_candidates": len(self.hls_candidates),
            "connections": len(self.connections),
            "ambiguities": len(self.ambiguities),
            "unsupported": len(self.unsupported),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": str(self.root),
            "counts": self.counts(),
            "files": [f.to_dict() for f in self.files],
            "modules": [u.to_dict() for u in self.units],
            "testbench_modules": [u.name for u in self.testbench_units],
            "instantiation_graph": {k: list(v) for k, v in self.instantiation_graph.items()},
            "instance_counts": self.instance_counts,
            "top_candidates": list(self.top_candidates),
            "clocks": [c.to_dict() for c in self.clocks],
            "resets": [r.to_dict() for r in self.resets],
            "hls_candidates": [h.to_dict() for h in self.hls_candidates],
            "connections": [c.to_dict() for c in self.connections],
            "ambiguities": [a.to_dict() for a in self.ambiguities],
            "unsupported": [u.to_dict() for u in self.unsupported],
            "external_inputs": list(self.external_inputs),
            "external_outputs": list(self.external_outputs),
        }


# ── The scanner ───────────────────────────────────────────────────────────

class ProjectDiscovery:
    """Walk a repository and report what FORGE can and cannot determine.

    Args:
        ignored_dirs: Directory names never descended into.
        max_files: Safety cap on how many files one scan will classify, so
            pointing ``forge adopt`` at a home directory by mistake stops
            rather than crawling it.
    """

    def __init__(
        self,
        *,
        ignored_dirs: Iterable[str] = _IGNORED_DIRS,
        max_files: int = 20000,
        opaque_modules: Iterable[str] = (),
        decisions: "Optional[ProjectDecisions]" = None,
    ) -> None:
        self.ignored_dirs = frozenset(ignored_dirs)
        self.max_files = max_files
        #: Everything the project's own ``forge.yml`` has already decided —
        #: which modules are opaque, which clock drives the design, which
        #: module is the top, which producer drives a contested consumer,
        #: which function is an HLS kernel's top. A question with an answer
        #: here is settled: discovery uses the answer and does not ask
        #: again. This is the only channel through which user intent
        #: reaches discovery, and it exists because these are decisions no
        #: source can state.
        self.decisions = decisions or ProjectDecisions()
        #: Backwards-compatible spelling of ``decisions.opaque_modules`` for
        #: callers that only ever had that one piece of intent to pass.
        self.opaque_modules = frozenset(opaque_modules) or self.decisions.opaque_modules

    # ── Entry point ───────────────────────────────────────────────────────

    def scan(self, root: "Path | str") -> DiscoveryResult:
        """Discover *root*. Reads only; writes nothing."""
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            raise NotADirectoryError(f"not a directory: {root_path}")

        result = DiscoveryResult(root=root_path)
        result.files = self._walk(root_path)
        self._scan_hdl(result)
        self._scan_hls(result)
        # Ranking runs before the clock/reset stage because that stage needs
        # to know which units are managed: a question about "the managed
        # design's clock" must not be raised by a module FORGE has already
        # excluded from it.
        self._rank_top_candidates(result)
        self._infer_clocks_and_resets(result)
        self._infer_connections(result)
        self._collect_unsupported(result)
        return result

    # ── Stage 1: source discovery + language classification ───────────────

    def _walk(self, root: Path) -> List[DiscoveredFile]:
        found: List[DiscoveredFile] = []
        for path in sorted(root.rglob("*")):
            if len(found) >= self.max_files:
                break
            if not path.is_file() or path.is_symlink():
                continue
            parts = set(path.relative_to(root).parts[:-1])
            if parts & self.ignored_dirs:
                continue
            role = self._classify(path, parts)
            if role is None:
                continue
            found.append(DiscoveredFile(path=path, role=role, language=_language_of(path)))
        return found

    def _classify(self, path: Path, parent_parts: set) -> Optional[str]:
        """What this file is for, or ``None`` to ignore it.

        Order matters: a ``.sv`` under ``tb/`` is a testbench first and RTL
        second, and anything under a build directory is output, not source.
        """
        suffix = path.suffix.lower()
        lowered_parts = {p.lower() for p in parent_parts}

        if lowered_parts & _BUILD_DIRS:
            return None
        if path.name.lower() in _BUILD_FILENAMES:
            return "build"
        if suffix in _CONSTRAINT_SUFFIXES:
            return "constraint"
        if suffix == ".tcl":
            return "build"

        is_testbench = bool(
            _TESTBENCH_NAME_RE.search(path.stem) or (lowered_parts & _TESTBENCH_DIRS)
        )
        if suffix in _RTL_SUFFIXES:
            return "testbench" if is_testbench else "rtl"
        if suffix in _HLS_SUFFIXES:
            return "testbench" if is_testbench else "hls"
        if suffix in _HEADER_SUFFIXES:
            return "header"
        if suffix in _DATA_SUFFIXES:
            return "data"
        return None

    # ── Stages 3-5: HDL units, ports, instantiation graph ─────────────────

    def _scan_hdl(self, result: DiscoveryResult) -> None:
        seen: set = set()
        for discovered in result.files:
            if discovered.role not in ("rtl", "testbench"):
                continue
            units = scan_hdl_file(discovered.path)
            target = result.units if discovered.role == "rtl" else result.testbench_units
            for unit in units:
                if unit.name in seen:
                    # Two files declaring the same module is a real problem,
                    # but it is `forge check`'s to report (ATG032) — discovery
                    # keeps the first and records nothing twice.
                    continue
                seen.add(unit.name)
                unit.opaque = unit.name in self.opaque_modules
                target.append(unit)
        result.instantiation_graph = resolve_instantiation_graph(result.units)

    # ── Stage 8: HLS candidates ───────────────────────────────────────────

    def _scan_hls(self, result: DiscoveryResult) -> None:
        """C++ sources that look like HLS kernels, and their likely top.

        A kernel is recognised by an ``#pragma HLS`` directive (proof) or by
        a file-scope function named after the file (a convention). The top
        function is the one whose name matches the file stem where such a
        function exists — the near-universal convention — and otherwise the
        only file-scope function defined. Anything else is ambiguous and
        recorded as a question rather than a guess.
        """
        for discovered in result.files:
            if discovered.role != "hls":
                continue
            try:
                text = discovered.path.read_text(errors="replace")
            except OSError:
                continue
            has_pragmas = bool(_HLS_PRAGMA_RE.search(text))
            functions = [m.group("name") for m in _CPP_FUNC_RE.finditer(text)]
            functions = [f for f in functions if f not in ("main",)]
            stem = discovered.path.stem

            declared_top = self.decisions.hls_tops.get(stem)
            if declared_top and declared_top in functions:
                top, confidence, rule = declared_top, DETERMINISTIC, "declared_in_forge_yml"
            elif stem in functions:
                top, confidence, rule = stem, INFERRED_CONFIDENCE, "function_named_after_file"
            elif len(functions) == 1:
                top, confidence, rule = functions[0], INFERRED_CONFIDENCE, "only_file_scope_function"
            elif not functions:
                continue
            else:
                result.ambiguities.append(Ambiguity(
                    id=f"hls-top:{result.root.name}:{discovered.path.name}",
                    classification=AMBIGUOUS,
                    subject=str(discovered.path),
                    kind=KIND_HLS_TOP,
                    question=(
                        f"{discovered.path.name} defines {len(functions)} functions and "
                        f"none is named after the file — which one is the HLS top?"
                    ),
                    options=tuple(sorted(functions)),
                    action=Action(
                        id="declare-hls-top",
                        description=(
                            f"Name the HLS top function for {discovered.path.name} — set "
                            f"`{discovered.path.stem}: {{top: <function>}}` under modules: "
                            f"in forge.yml, or answer it with `forge adopt --interactive`"
                        ),
                        auto_fixable=False,
                        safe=False,
                    ),
                ))
                continue

            if not has_pragmas and rule == "only_file_scope_function":
                # One plain C++ function with no HLS directives is more
                # likely a helper than a kernel. Not recorded as a kernel.
                continue

            result.hls_candidates.append(HlsCandidate(
                path=discovered.path,
                top=top,
                has_pragmas=has_pragmas,
                evidence=(
                    Evidence(str(discovered.path), rule, top, confidence),
                    Evidence(
                        str(discovered.path), "hls_pragma_present", has_pragmas,
                        DETERMINISTIC if has_pragmas else HEURISTIC,
                    ),
                ),
            ))

    # ── Stage 6: clock/reset candidates ───────────────────────────────────

    def _infer_clocks_and_resets(self, result: DiscoveryResult) -> None:
        """Group every clock-shaped and reset-shaped port by name.

        Grouping by name rather than by module is what makes the result
        useful: one entry named ``clk`` appearing on every module is a
        single-clock design, while two entries is a fact the user needs to
        see before FORGE claims to manage the design.
        """
        clocks: Dict[str, List[str]] = {}
        resets: Dict[str, List[str]] = {}
        for unit in result.units:
            for port in unit.clock_ports:
                clocks.setdefault(port, []).append(f"{unit.name}.{port}")
            for port in unit.reset_ports:
                resets.setdefault(port, []).append(f"{unit.name}.{port}")

        for name, ports in sorted(clocks.items()):
            result.clocks.append(SignalCandidate(
                name=name,
                ports=tuple(ports),
                evidence=(
                    Evidence(ports[0].split(".")[0], "port_name_convention", name, INFERRED_CONFIDENCE),
                    Evidence(str(result.root), "appears_on_modules", len(ports), DETERMINISTIC),
                ),
            ))
        for name, ports in sorted(resets.items()):
            result.resets.append(SignalCandidate(
                name=name,
                ports=tuple(ports),
                active_low=is_active_low(name),
                evidence=(
                    Evidence(ports[0].split(".")[0], "port_name_convention", name, INFERRED_CONFIDENCE),
                    Evidence(
                        ports[0].split(".")[0], "active_level_from_name",
                        "low" if is_active_low(name) else "high", HEURISTIC,
                    ),
                ),
            ))

        # Only a clock that reaches a module FORGE actually manages can be
        # *the* functional clock. A vendor IP excluded as unsupported still
        # has its clocks reported above — they are real and worth seeing —
        # but they must not turn into a question about a design they are no
        # longer part of.
        managed_clocks = result.managed_clocks
        # A clock the project has already named is not a question. The
        # answer has to be one FORGE actually found, though: a `clock.port`
        # naming a port no managed module has is a broken config, and
        # silently treating it as settled would hide that — the question
        # stays, and `forge check`'s own clock check reports the mismatch.
        declared_clock = self.decisions.clock_port
        clock_is_settled = declared_clock in {c.name for c in managed_clocks}
        if len(managed_clocks) > 1 and not clock_is_settled:
            result.ambiguities.append(Ambiguity(
                id="clock:multiple-candidates",
                classification=AMBIGUOUS,
                subject="clock",
                kind=KIND_CLOCK,
                question=(
                    f"{len(managed_clocks)} distinct clock port names were found on the "
                    f"managed modules ({', '.join(c.name for c in managed_clocks)}) — "
                    f"which one drives the design?"
                ),
                options=tuple(c.name for c in managed_clocks),
                action=Action(
                    id="declare-clock",
                    description=(
                        "Set clock.port in forge.yml to the design's functional clock, "
                        "or answer it with `forge adopt --interactive`"
                    ),
                    auto_fixable=False,
                    safe=False,
                ),
                evidence=tuple(e for c in managed_clocks for e in c.evidence),
            ))

    # ── Stage 7: top-level candidates ─────────────────────────────────────

    def _rank_top_candidates(self, result: DiscoveryResult) -> None:
        """Order the uninstantiated units by how top-like they are.

        A unit nobody instantiates is a candidate; among candidates, one
        named ``*_top``/``top`` that instantiates other units is far more
        likely than a leaf block nobody happens to use yet. The ranking is a
        display and defaulting aid only — a tie that actually matters is
        raised as an ambiguity below.
        """
        graph = result.instantiation_graph
        instantiated = {dep for deps in graph.values() for dep in deps}
        candidates = [u for u in result.units if u.name not in instantiated]

        def rank(unit: HdlUnit) -> Tuple[int, int, int, str]:
            name_score = 2 if unit.name.lower() in ("top", "top_level") else (
                1 if "top" in unit.name.lower() else 0
            )
            return (
                -name_score,
                -len(distinct_dependencies(graph, unit.name)),
                -len(unit.ports),
                unit.name,
            )

        result.top_candidates = [u.name for u in sorted(candidates, key=rank)]

        # A declared top wins the ranking outright: `structural_top` reads
        # the first candidate that instantiates others, so declaring one has
        # to move it to the front, not merely silence the question.
        declared_top = self.decisions.top
        if declared_top and declared_top in result.top_candidates:
            result.top_candidates = [declared_top] + [
                n for n in result.top_candidates if n != declared_top
            ]

        structural = [n for n in result.top_candidates if graph.get(n)]
        top_is_settled = declared_top in structural
        if len(structural) > 1 and not top_is_settled:
            result.ambiguities.append(Ambiguity(
                id="top:multiple-candidates",
                classification=AMBIGUOUS,
                subject="top",
                kind=KIND_TOP,
                question=(
                    f"{len(structural)} modules instantiate others and are themselves "
                    f"instantiated by nobody — which is the design top level?"
                ),
                options=tuple(structural),
                action=Action(
                    id="declare-top",
                    description=(
                        "Set top: in forge.yml to the design's top-level module, or "
                        "answer it with `forge adopt --interactive`"
                    ),
                    auto_fixable=False,
                    safe=False,
                ),
            ))

    # ── Stage 12: topology inference ──────────────────────────────────────

    def _wire_declared(
        self, result: DiscoveryResult, managed: List[HdlUnit],
    ) -> Tuple[set, set]:
        """Wire the connections ``forge.yml`` declares, and report the ones
        it cannot.

        A declared pair is the user's answer to a question FORGE asked, so
        it is wired as ``deterministic`` — not because a name convention
        matched, but because someone said so. Returns the producer and
        consumer endpoints that are now spoken for, so inference leaves them
        alone rather than proposing a second reading of a settled question.

        A declaration naming a port that does not exist is *not* silently
        dropped: it becomes a blocking question of its own. Quietly ignoring
        it would leave the user believing a connection exists — the same
        class of silent wrongness the whole mechanism exists to prevent, and
        the likeliest cause is a typo in an answer they gave.
        """
        ports_by_endpoint = {
            f"{unit.name}.{port.name}": (unit, port)
            for unit in managed
            for port in (*unit.inputs(), *unit.outputs())
        }

        producers: set = set()
        consumers: set = set()
        for producer, consumer in self.decisions.connections:
            missing = [e for e in (producer, consumer) if e not in ports_by_endpoint]
            if missing:
                result.ambiguities.append(_undeliverable_declaration(
                    producer, consumer, missing, sorted(ports_by_endpoint),
                ))
                continue
            unit, port = ports_by_endpoint[producer]
            result.connections.append(InferredConnection(
                producer=producer,
                consumer=consumer,
                width=port.width,
                evidence=(
                    Evidence(str(result.root / ROOT_CONFIG_NAME), "declared_connection",
                             f"{producer} -> {consumer}", DETERMINISTIC),
                    Evidence(str(unit.path), "port_width", port.width, DETERMINISTIC),
                ),
            ))
            producers.add(producer)
            consumers.add(consumer)
        return producers, consumers

    def _infer_connections(self, result: DiscoveryResult) -> None:
        """Match producer outputs to consumer inputs, conservatively.

        Two ports are a candidate pair when their widths agree *and* their
        names agree once direction affixes are removed (``hit_out`` and
        ``hit_in`` both normalise to ``hit``). Width alone is never enough:
        in any real design most 32-bit outputs could be wired to most 32-bit
        inputs, which is precisely the ambiguity the plan's ``E231`` example
        refuses to resolve by guessing.

        Exactly one candidate consumer produces a connection. More than one
        produces an :class:`Ambiguity` listing every candidate and no
        connection at all — unless the project has already answered that
        question in ``forge.yml``, in which case the declared pair is wired
        and nothing is asked.
        """
        managed = result.managed_units
        inputs_by_key: Dict[Tuple[str, int], List[str]] = {}
        for unit in managed:
            for port in unit.inputs():
                inputs_by_key.setdefault(
                    (_normalise_port(port.name), port.width), []
                ).append(f"{unit.name}.{port.name}")

        declared_producers, declared_consumers = self._wire_declared(result, managed)

        # Pass 1: each output's candidate consumers. An output with more
        # than one is already undecided and is asked about here.
        tentative: List[Tuple[HdlUnit, Any, str, str]] = []
        for unit in managed:
            for port in unit.outputs():
                key = (_normalise_port(port.name), port.width)
                producer = f"{unit.name}.{port.name}"
                if producer in declared_producers:
                    # Decided. Its consumer is already wired above, and a
                    # second inferred consumer for the same output would
                    # contradict the answer the user gave.
                    continue
                candidates = [
                    endpoint for endpoint in inputs_by_key.get(key, [])
                    if not endpoint.startswith(f"{unit.name}.")
                    and endpoint not in declared_consumers
                ]
                if len(candidates) == 1:
                    tentative.append((unit, port, producer, candidates[0]))
                elif len(candidates) > 1:
                    result.ambiguities.append(_ambiguous_consumer(unit, port, producer, candidates))

        # Pass 2: the other direction. Two producers whose sole candidate is
        # the *same* input would each look unambiguous on their own, and
        # wiring both would put two drivers on one net — a design that
        # elaborates and is wrong in exactly the way this whole mechanism
        # exists to prevent. So a contested consumer is a question too, and
        # neither producer is wired.
        by_consumer: Dict[str, List[Tuple[HdlUnit, Any, str, str]]] = {}
        for entry in tentative:
            by_consumer.setdefault(entry[3], []).append(entry)

        consumed: set = set()
        produced: set = set()
        for consumer, entries in sorted(by_consumer.items()):
            if len(entries) > 1:
                producers = sorted(entry[2] for entry in entries)
                result.ambiguities.append(Ambiguity(
                    id=f"connection:{consumer}",
                    classification=AMBIGUOUS,
                    subject=consumer,
                    kind=KIND_CONNECTION,
                    question=(
                        f"{consumer} has {len(producers)} equally valid producers — "
                        f"wiring all of them would put several drivers on one net"
                    ),
                    options=tuple(producers),
                    action=Action(
                        id="resolve-ambiguous-producer",
                        description=(
                            f"Declare which of {', '.join(producers)} drives {consumer} — "
                            f"add the pair under connections: in forge.yml, or answer it "
                            f"with `forge adopt --interactive`"
                        ),
                        auto_fixable=False,
                        safe=False,
                    ),
                    evidence=(
                        Evidence(consumer, "candidate_producer_count", len(producers), DETERMINISTIC),
                    ),
                ))
                continue

            unit, port, producer, _ = entries[0]
            result.connections.append(InferredConnection(
                producer=producer,
                consumer=consumer,
                width=port.width,
                evidence=(
                    Evidence(str(unit.path), "port_width", port.width, DETERMINISTIC),
                    Evidence(str(unit.path), "direction", "output_to_input", DETERMINISTIC),
                    Evidence(
                        str(unit.path), "normalised_name_match",
                        _normalise_port(port.name), INFERRED_CONFIDENCE,
                    ),
                ),
            ))
            consumed.add(consumer)
            produced.add(producer)

        result.connections.sort(key=lambda c: (c.producer, c.consumer))

        # Anything left unmatched becomes a top-level port rather than an
        # error: a pipeline's first input and last output are supposed to
        # reach the outside world.
        for unit in managed:
            for port in unit.inputs():
                endpoint = f"{unit.name}.{port.name}"
                if endpoint not in consumed:
                    result.external_inputs.append(endpoint)
            for port in unit.outputs():
                endpoint = f"{unit.name}.{port.name}"
                if endpoint not in produced:
                    result.external_outputs.append(endpoint)

    # ── Stage 13: unsupported constructs ──────────────────────────────────

    def _collect_unsupported(self, result: DiscoveryResult) -> None:
        """Turn scan-time limitations into questions with real options.

        The plan's Phase D3: a user must learn that a module is outside
        FORGE's current envelope during adoption, with the ways forward
        spelled out, rather than after authoring contracts for it.
        """
        for unit in result.units:
            if unit.opaque:
                # The user has already answered this question. The reason is
                # still worth recording — it is why the module is opaque —
                # but it is no longer an open decision.
                continue
            for reason in unit.unsupported:
                result.unsupported.append(Ambiguity(
                    id=f"unsupported:{unit.name}",
                    classification=UNSUPPORTED,
                    subject=unit.name,
                    kind=KIND_MODULE,
                    question=f"{unit.name}: {reason}",
                    options=(
                        f"import {unit.name} as an opaque module (structure only): add "
                        f"`{unit.name}: {{management: opaque}}` under `modules:` in "
                        f"forge.yml and re-run `forge adopt`",
                        f"split {unit.name} into one FORGE-managed wrapper per clock domain",
                        f"exclude {unit.name} from the FORGE-managed topology",
                    ),
                    action=Action(
                        id="resolve-unsupported-module",
                        description=(
                            f"Choose how {unit.name} is handled — it is outside what FORGE "
                            f"currently models, so it was left out of the generated "
                            f"project. The usual answer is to declare it opaque: add "
                            f"`{unit.name}: {{management: opaque}}` under `modules:` in "
                            f"forge.yml, then re-run `forge adopt`"
                        ),
                        auto_fixable=False,
                        safe=False,
                    ),
                    evidence=(Evidence(str(unit.path), "scan_limitation", reason, DETERMINISTIC),),
                ))


# ── Helpers ───────────────────────────────────────────────────────────────

def _undeliverable_declaration(
    producer: str, consumer: str, missing: List[str], known: List[str],
) -> Ambiguity:
    """A ``connections:`` entry naming a port no managed module has."""
    return Ambiguity(
        id=f"connection:declared:{producer}->{consumer}",
        classification=AMBIGUOUS,
        subject=missing[0],
        kind=KIND_CONNECTION,
        question=(
            f"forge.yml declares {producer} -> {consumer}, but "
            f"{' and '.join(missing)} is not a port of any managed module"
        ),
        options=tuple(known),
        action=Action(
            id="fix-declared-connection",
            description=(
                f"Correct or remove the `{producer} -> {consumer}` entry under "
                f"connections: in forge.yml — the endpoint is written "
                f"'module.port' and both must exist"
            ),
            auto_fixable=False,
            safe=False,
        ),
        evidence=(Evidence(ROOT_CONFIG_NAME, "declared_endpoint_missing", missing[0], DETERMINISTIC),),
    )


def _ambiguous_consumer(unit: HdlUnit, port, producer: str, candidates: List[str]) -> Ambiguity:
    """The plan's E231 case: one producer, several equally valid consumers."""
    return Ambiguity(
        id=f"connection:{producer}",
        classification=AMBIGUOUS,
        subject=producer,
        kind=KIND_CONNECTION,
        question=(
            f"{producer} has {len(candidates)} equally valid consumers — width and "
            f"direction match all of them and no semantic family separates them"
        ),
        options=tuple(sorted(candidates)),
        action=Action(
            id="resolve-ambiguous-consumer",
            description=(
                f"Declare the intended consumer for {producer} — add the pair under "
                f"connections: in forge.yml, or answer it with `forge adopt "
                f"--interactive`"
            ),
            auto_fixable=False,
            safe=False,
        ),
        evidence=(
            Evidence(str(unit.path), "port_width", port.width, DETERMINISTIC),
            Evidence(str(unit.path), "candidate_count", len(candidates), DETERMINISTIC),
        ),
    )


_DIRECTION_AFFIXES = ("_out", "_o", "_in", "_i", "_input", "_output", "_src", "_dst")
_DIRECTION_PREFIXES = ("o_", "i_", "in_", "out_")


def _normalise_port(name: str) -> str:
    """Strip direction affixes so a producer and consumer name can match.

    ``candidate_out``/``candidate_in``/``o_candidate`` all normalise to
    ``candidate``. Stripping is single-pass and never empties the name — a
    port genuinely called ``out`` keeps its name rather than becoming a
    wildcard that matches everything.
    """
    lowered = name.lower()
    for prefix in _DIRECTION_PREFIXES:
        if lowered.startswith(prefix) and len(lowered) > len(prefix):
            lowered = lowered[len(prefix):]
            break
    for suffix in _DIRECTION_AFFIXES:
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            lowered = lowered[: -len(suffix)]
            break
    return lowered


def _language_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".sv", ".svh"):
        return "systemverilog"
    if suffix in (".v", ".vh"):
        return "verilog"
    if suffix in (".vhd", ".vhdl"):
        return "vhdl"
    if suffix in _HLS_SUFFIXES | _HEADER_SUFFIXES:
        return "cpp"
    return ""
