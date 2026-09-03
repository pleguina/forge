"""Turn a discovered repository into a FORGE project, without guessing.

:mod:`forge.project.discovery` works out what is in a repository. This
module writes the project files that follow from it: a root ``forge.yml``
the user maintains, and under ``.forge/`` the expanded module registry,
design topology and interface contracts FORGE maintains.

The line this module must not cross is the plan's: *never silently convert an
uncertain guess into contract truth*. So:

* Anything a source proves — a port's width and direction, which module
  instantiates which — is written.
* A convention that resolved unambiguously — one clock named ``clk``, one
  candidate consumer for an output — is written **and** recorded with the
  evidence that produced it, so the user can find and reverse it.
* Anything genuinely ambiguous — two equally valid consumers, three
  competing tops — is written **nowhere**. It comes back as an unresolved
  decision for ``forge check`` to report and the user to settle.

Contracts are emitted through ``infer_contract_skeleton`` — the same
generator ``forge contract infer`` uses — so an adopted contract and a
hand-authored one are the same artifact, and the semantic fields that cannot
be inferred (``wiring_kind``, ``protocol``, ``coordinates``) stay absent
rather than being invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from forge.project.actions import Action
from forge.project.config import (
    DEFAULT_PART,
    FORGE_YML_SCHEMA_VERSION,
    MANAGEMENT_MANAGED,
    MANAGEMENT_OPAQUE,
    ClockSpec,
    DeclaredConnection,
    ForgeConfig,
    ModulePolicy,
    ProjectDecisions,
    ResetSpec,
)
from forge.project.discovery import Ambiguity, DiscoveryResult, ProjectDiscovery
from forge.project.evidence import DETERMINISTIC, INFERRED, Evidence
from forge.project.hdl_scan import HdlUnit
from forge.project.paths import ROOT_CONFIG_NAME, ProjectPaths


@dataclass
class AdoptionPlan:
    """What adoption would write, before anything is written.

    Held as ``{path: content}`` so ``--dry-run`` and the real run are the
    same computation — the preview cannot drift from what actually lands,
    because it *is* what actually lands.
    """

    root: Path
    config: ForgeConfig
    discovery: DiscoveryResult
    files: "Dict[Path, str]" = field(default_factory=dict)
    unresolved: List[Ambiguity] = field(default_factory=list)
    decisions: List[Evidence] = field(default_factory=list)
    skipped_modules: List[str] = field(default_factory=list)

    @property
    def paths(self) -> ProjectPaths:
        return self.config.paths()

    def write(self, *, overwrite: bool = False) -> List[Path]:
        """Write the plan. Returns the paths written, in order.

        An existing ``forge.yml`` is never overwritten without *overwrite* —
        it is the one file in the project the user owns, and silently
        replacing their edits during a re-scan would be the worst possible
        behaviour for a command people are told to re-run.
        """
        written: List[Path] = []
        for path, content in self.files.items():
            if path.exists() and not overwrite and path.name == ROOT_CONFIG_NAME:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            written.append(path)
        return written

    def summary(self) -> Dict[str, Any]:
        return {
            "project": self.config.project,
            "root": str(self.root),
            "modules": len(self.discovery.managed_units),
            "connections": len(self.discovery.connections),
            "files": [str(p) for p in self.files],
            "unresolved_decisions": len(self.unresolved),
            "skipped_modules": list(self.skipped_modules),
        }


def plan_adoption(
    root: "Path | str",
    *,
    project_name: Optional[str] = None,
    part: str = DEFAULT_PART,
    discovery: Optional[DiscoveryResult] = None,
) -> AdoptionPlan:
    """Work out the whole adoption without touching the filesystem.

    Args:
        root: Repository to adopt.
        project_name: Overrides the directory name.
        part: Target device. Not inferable from sources — a project adopted
            without one gets a documented placeholder, not a silent guess.
        discovery: A scan already performed, to avoid walking twice.
    """
    root_path = Path(root).expanduser().resolve()
    decided = _declared_decisions(root_path)
    if discovery is None:
        # Re-adopting a project reads back every decision the sources cannot
        # state — which modules are opaque, which clock drives the design,
        # which module is the top, which producer drives a contested
        # consumer. Without this, every re-run would undo them.
        discovery = ProjectDiscovery(decisions=decided).scan(root_path)
    result = discovery

    config = _build_config(
        root_path, result, project_name=project_name, part=part, decided=decided,
    )
    paths = config.paths()

    plan = AdoptionPlan(root=root_path, config=config, discovery=result)
    plan.unresolved = list(result.blocking_ambiguities) + list(result.unsupported)
    plan.skipped_modules = sorted(
        u.name for u in result.units if u.unsupported
    )

    managed = result.managed_units
    plan.files[paths.config] = config.to_yaml()
    if managed:
        plan.files[paths.modules_yml] = _render_modules_yml(config, paths, managed, result)
        plan.files[paths.design_yml] = _render_design_yml(config, managed, result)
        for unit in managed:
            plan.files[paths.contract_root / f"{unit.name}.interface.yaml"] = (
                _render_contract(unit)
            )
    plan.files[paths.state_root / ".gitignore"] = _STATE_GITIGNORE
    plan.decisions = _decision_log(config, result)
    return plan


# ── forge.yml ─────────────────────────────────────────────────────────────

def _declared_decisions(root: Path) -> ProjectDecisions:
    """Every decision an existing ``forge.yml`` records.

    Best-effort: a project with no config, or one that will not parse, has
    declared nothing. ``forge check`` is where a broken config is reported —
    adoption must not fail on one, since re-running adoption is exactly what
    someone does to repair a project.
    """
    try:
        return ForgeConfig.load(root / ROOT_CONFIG_NAME).decisions()
    except Exception:  # noqa: BLE001
        return ProjectDecisions()


def _build_config(
    root: Path,
    result: DiscoveryResult,
    *,
    project_name: Optional[str],
    part: str,
    decided: Optional[ProjectDecisions] = None,
) -> ForgeConfig:
    """Derive the root config from what discovery found.

    The clock and reset ports are written only when discovery found exactly
    one candidate of each. With several, the defaults stay in place and the
    ambiguity discovery already raised is what the user is asked to settle —
    writing the first candidate would be exactly the silent guess the plan
    forbids.
    """
    # Only signals reaching a module FORGE manages may be written into
    # forge.yml — configuring the design around a clock that exists solely
    # on an excluded vendor IP would be worse than leaving the default.
    decided = decided or ProjectDecisions()
    known_clocks = {c.name for c in result.managed_clocks}
    known_resets = {r.name: r for r in result.managed_resets}

    clock = ClockSpec()
    clock_candidates = result.managed_clocks
    if decided.clock_port in known_clocks:
        # The user already answered "which clock drives this design?" — a
        # re-adoption must return their answer, not the state before it.
        clock = ClockSpec(port=decided.clock_port)
    elif len(clock_candidates) == 1:
        clock = ClockSpec(port=clock_candidates[0].name)
    reset = ResetSpec()
    reset_candidates = result.managed_resets
    if decided.reset_port in known_resets:
        found = known_resets[decided.reset_port]
        reset = ResetSpec(port=found.name, active="low" if found.active_low else "high")
    elif len(reset_candidates) == 1:
        found = reset_candidates[0]
        reset = ResetSpec(port=found.name, active="low" if found.active_low else "high")

    return ForgeConfig(
        project=project_name or _slug(root.name),
        root=root,
        # Carried through verbatim: this is user intent, and adoption
        # regenerates everything *except* what the user decided.
        modules=_module_policies(result, decided),
        # Answers, carried through verbatim for the same reason: a decision
        # the user made once must survive every re-adoption.
        connections=[
            DeclaredConnection(producer=p, consumer=c) for p, c in decided.connections
        ],
        rtl_globs=_source_globs(root, result, "rtl"),
        hls_globs=_source_globs(root, result, "hls"),
        top=decided.top or result.structural_top,
        clock=clock,
        reset=reset,
        part=part,
        schema_version=FORGE_YML_SCHEMA_VERSION,
    )


def _module_policies(
    result: DiscoveryResult, decided: ProjectDecisions,
) -> Dict[str, ModulePolicy]:
    """The ``modules:`` block: what the user decided about each module.

    Two kinds of intent live here, and both are the user's: which modules
    are opaque (carried through from the previous config via
    ``result.opaque_units``) and which function is an HLS kernel's top.
    """
    policies: Dict[str, ModulePolicy] = {
        name: ModulePolicy(management=MANAGEMENT_OPAQUE)
        for name in sorted(unit.name for unit in result.opaque_units)
    }
    for name, top in sorted(decided.hls_tops.items()):
        existing = policies.get(name)
        policies[name] = ModulePolicy(
            management=existing.management if existing else MANAGEMENT_MANAGED,
            top=top,
        )
    return policies


def _source_globs(root: Path, result: DiscoveryResult, role: str) -> List[str]:
    """One glob per directory that actually holds sources of *role*.

    Directory-scoped globs rather than one repository-wide ``**`` so the
    config states where the design *is* — and so adding a file to an
    unrelated directory later does not silently pull it into the design.
    """
    directories: Dict[str, set] = {}
    for discovered in result.files_by_role(role):
        try:
            relative = discovered.path.relative_to(root)
        except ValueError:
            continue
        parent = str(relative.parent) if str(relative.parent) != "." else ""
        directories.setdefault(parent, set()).add(discovered.path.suffix.lower())

    globs: List[str] = []
    for directory, suffixes in sorted(directories.items()):
        for suffix in sorted(suffixes):
            prefix = f"{directory}/" if directory else ""
            globs.append(f"{prefix}*{suffix}")
    return globs


def _slug(name: str) -> str:
    """A project name safe to use in generated identifiers."""
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name).strip("_")
    return cleaned or "project"


# ── .forge/project/modules.yml ────────────────────────────────────────────

def _render_modules_yml(
    config: ForgeConfig,
    paths: ProjectPaths,
    managed: Sequence[HdlUnit],
    result: DiscoveryResult,
) -> str:
    """The expanded module registry.

    Every field here is derived: the language from the file suffix, the top
    from the module's own declaration, the source path from where the file
    was found. No ``latency_hint`` is emitted — latency is a property of the
    implementation that no port list reveals, and a fabricated one would
    silently corrupt the latency check.
    """
    import yaml

    modules: List[Dict[str, Any]] = []
    for unit in managed:
        entry: Dict[str, Any] = {
            "name": unit.name,
            "kind": "rtl",
            "rtl_lang": "vhdl" if unit.language == "vhdl" else "verilog",
            "top": unit.name,
            "interface_contract": f"../contracts/{unit.name}.interface.yaml",
            "src": [_relative_to(unit.path, paths.project_config_root)],
        }
        modules.append(entry)
        if unit.opaque:
            # Recorded for the reader of the generated registry, who would
            # otherwise have no way to tell that FORGE is deliberately not
            # modelling this module's internals.
            entry["notes"] = (
                "opaque: integrated structurally; FORGE does not model this "
                "module's internal clock structure or implementation"
            )

    document = {
        "registry_version": "1",
        "defaults": {
            "part": config.part,
            "clock_period": config.clock_period_ns(),
            "vendor": config.project,
            "version": "1.0",
        },
        "modules": modules,
    }
    return _GENERATED_HEADER.format(
        what="module registry",
        project=config.project,
    ) + yaml.safe_dump(document, sort_keys=False, default_flow_style=False)


# ── .forge/project/design.yml ─────────────────────────────────────────────

def _render_design_yml(
    config: ForgeConfig,
    managed: Sequence[HdlUnit],
    result: DiscoveryResult,
) -> str:
    """The expanded design topology.

    Connections are emitted as explicit ``port_map`` pairs rather than as
    topology groups or ``contract_wiring``. That is deliberate: a port_map
    pair states exactly which two ports are joined and can be read and
    corrected by someone who has never heard of a topology group, which is
    the entire point of adoption. Richer forms remain available to anyone
    who later needs them.
    """
    import yaml

    instances = {unit.name: unit.name for unit in managed}
    externals_in: Dict[str, List[str]] = {}
    externals_out: Dict[str, List[str]] = {}
    for endpoint in result.external_inputs:
        module, port = endpoint.split(".", 1)
        if module in instances:
            externals_in.setdefault(module, []).append(port)
    for endpoint in result.external_outputs:
        module, port = endpoint.split(".", 1)
        if module in instances:
            externals_out.setdefault(module, []).append(port)

    counts = result.instance_counts
    module_entries: List[Dict[str, Any]] = []
    for unit in managed:
        entry: Dict[str, Any] = {
            "name": unit.name,
            "ref": unit.name,
            # Proven by the existing top level's own instantiations where
            # there is one; 1 where there is nothing to prove otherwise.
            "instances": counts.get(unit.name, 1),
        }
        inputs = list(externals_in.get(unit.name, ()))
        if unit.opaque:
            # An opaque module's clock and reset pins other than the design's
            # own must reach the generated top level, so the enclosing design
            # drives them. Left to the global clock/reset fan-out they would
            # all be tied to one ap_clk/ap_rst net, silently shorting
            # distinct domains together — which is the whole reason a
            # multi-clock module could not be integrated before.
            inputs.extend(unit.unmanaged_clock_ports(config.clock.port))
            inputs.extend(unit.unmanaged_reset_ports(config.reset.port))
        if inputs:
            entry["external_in_ports"] = sorted(set(inputs))
        if externals_out.get(unit.name):
            entry["external_out_ports"] = sorted(externals_out[unit.name])
        module_entries.append(entry)

    pairs_by_edge: Dict[Tuple[str, str], List[List[str]]] = {}
    for connection in result.connections:
        src_module, src_port = connection.producer.split(".", 1)
        dst_module, dst_port = connection.consumer.split(".", 1)
        if src_module not in instances or dst_module not in instances:
            continue
        pairs_by_edge.setdefault((src_module, dst_module), []).append([src_port, dst_port])

    connections = [
        {"from": src, "to": dst, "port_map": sorted(pairs)}
        for (src, dst), pairs in sorted(pairs_by_edge.items())
    ]

    document: Dict[str, Any] = {
        "schema_version": "1.0",
        "part": config.part,
        "clock_period": config.clock_period_ns(),
        "block_protocol": "none",
        "connect_clock": True,
        "connect_reset": True,
        "registry": "modules.yml",
        "modules": module_entries,
    }
    if connections:
        document["connections"] = connections

    return _GENERATED_HEADER.format(
        what="design topology",
        project=config.project,
    ) + yaml.safe_dump(document, sort_keys=False, default_flow_style=False)


# ── .forge/contracts/*.interface.yaml ─────────────────────────────────────

def _render_contract(unit: HdlUnit) -> str:
    """One module's interface contract, from its real ports.

    Delegates to the generator ``forge contract infer`` uses, so an adopted
    contract is byte-identical to one the user would have generated by hand
    — including its ``normalization_status: draft``, which is what makes
    ``forge topgen validate --strict`` flag it as unreviewed.
    """
    from forge.generation.migrate import infer_contract_skeleton

    entry = {
        "ports": [
            {
                "name": port.name,
                "direction": "IN" if port.direction == "in" else "OUT",
                "width": port.width,
            }
            for port in unit.ports.values()
        ]
    }
    return infer_contract_skeleton(unit.name, entry, source_type="rtl")


# ── Decision log ──────────────────────────────────────────────────────────

def _decision_log(config: ForgeConfig, result: DiscoveryResult) -> List[Evidence]:
    """Every automated decision adoption made, with its confidence.

    This is what makes adoption reversible: a user who disagrees with a
    conclusion can see which rule produced it and what it rested on, rather
    than having to reverse-engineer generated YAML.
    """
    decisions: List[Evidence] = []
    clock = next((c for c in result.clocks if c.name == config.clock.port), None)
    if clock is not None:
        decisions.append(Evidence(
            source=clock.ports[0],
            rule="single_clock_candidate",
            value=config.clock.port,
            confidence=INFERRED,
        ))
    reset = next((r for r in result.resets if r.name == config.reset.port), None)
    if reset is not None:
        decisions.append(Evidence(
            source=reset.ports[0],
            rule="single_reset_candidate",
            value=f"{config.reset.port} (active {config.reset.active})",
            confidence=INFERRED,
        ))
    if result.structural_top:
        decisions.append(Evidence(
            source=str(result.root),
            rule="uninstantiated_structural_module",
            value=result.structural_top,
            confidence=INFERRED,
        ))
    for connection in result.connections:
        decisions.append(Evidence(
            source=connection.producer,
            rule="sole_matching_consumer",
            value=connection.consumer,
            confidence=connection.confidence,
        ))
    if result.structural_top:
        for name, count in sorted(result.instance_counts.items()):
            if count > 1:
                decisions.append(Evidence(
                    source=result.structural_top,
                    rule="instances_counted_in_top_level",
                    value=f"{name}: {count}",
                    confidence=DETERMINISTIC,
                ))
    for unit in result.managed_units:
        decisions.append(Evidence(
            source=str(unit.path),
            rule="ports_read_from_source",
            value=f"{unit.name}: {len(unit.ports)} ports",
            confidence=DETERMINISTIC,
        ))
    for unit in result.opaque_units:
        decisions.append(Evidence(
            source=str(config.root / ROOT_CONFIG_NAME),
            rule="declared_opaque_by_user",
            value=f"{unit.name}: {'; '.join(unit.unsupported) or 'integrated structurally'}",
            confidence=DETERMINISTIC,
        ))
    return decisions


def adoption_actions(plan: AdoptionPlan) -> List[Action]:
    """What the user should do next, given what adoption could not settle."""
    actions: List[Action] = []
    for ambiguity in plan.unresolved:
        if ambiguity.action:
            actions.append(ambiguity.action)
    actions.append(Action(
        id="check-adopted-project",
        description="Review what FORGE inferred and what it still needs",
        command="forge check",
        auto_fixable=False,
    ))
    return actions


# ── Text fragments ────────────────────────────────────────────────────────

_GENERATED_HEADER = """\
# ─────────────────────────────────────────────────────────────────────────
# {project} — {what} (generated)
# ─────────────────────────────────────────────────────────────────────────
# Generated by `forge adopt` from the project's own sources. Edits here are
# overwritten on the next adopt; change forge.yml instead, or move a
# decision you want to keep into it.
#
# Every field below was read from a source file. Nothing FORGE could not
# determine has been filled in with a guess — run `forge check` for the
# decisions still outstanding.
# ─────────────────────────────────────────────────────────────────────────
"""

_STATE_GITIGNORE = """\
# Everything under .forge/ is generated from forge.yml and the project's
# sources, and can be regenerated with `forge adopt`. Nothing here is
# authored, so nothing here needs to be committed.
*
"""


def _relative_to(path: Path, base: Path) -> str:
    """*path* written relative to *base*, staying inside the project.

    Uses ``os.path.relpath`` semantics so a source that lives *above* the
    generated-config directory (the normal case: sources at the repository
    root, config under ``.forge/project/``) comes out as ``../../rtl/x.v``
    rather than as an absolute path that breaks the moment the repository is
    cloned somewhere else.
    """
    import os

    return os.path.relpath(str(path), str(base))
