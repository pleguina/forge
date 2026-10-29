"""One model of project health. Two commands render it.

``forge check`` asks "what is wrong with this project?" and ``forge next``
asks "what should I do about it?". Those are two views of one answer, and
the plan is explicit that they must not grow two rule systems: *both
commands render different views of the same object*.

:class:`ProjectStatus` is that object. :func:`evaluate` builds it by running
every check in :data:`CHECKS` against a resolved project, and each check
returns sections, diagnostics and actions together — so a blocker can never
be reported without the step that clears it, which is the plan's stated
acceptance criterion ("for every blocking diagnostic there must be at least
one actionable ``next_action``").

Diagnostics are ``ATGDiagnostic``s with stable ``ATG03x``/``ATG04x`` codes,
not a parallel diagnostic system — so ``forge check --json`` lands in the
same envelope, with the same severities, as every other command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from forge.core.diagnostics import ATGDiagnosticReport
from forge.project.actions import Action, dedupe
from forge.project.config import ForgeConfig, ModulePolicy
from forge.project.discovery import (
    AMBIGUOUS,
    KIND_CONNECTION,
    KIND_HLS_TOP,
    KIND_TOP,
    DiscoveryResult,
    ProjectDiscovery,
)
from forge.project.paths import ROOT_CONFIG_NAME, ProjectPaths, find_project_root

# ── Section states ────────────────────────────────────────────────────────

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
PARTIAL = "PARTIAL"
NOT_CONFIGURED = "NOT CONFIGURED"
STALE = "STALE"

#: How much of "this project is fully described" each state represents.
#: Drives the completion percentage ``forge next`` reports — a number that
#: only means anything if it moves monotonically as the user resolves
#: things, hence explicit weights rather than a pass/fail count.
_COMPLETION = {
    PASS: 1.0,
    WARN: 0.75,
    PARTIAL: 0.5,
    STALE: 0.5,
    NOT_CONFIGURED: 0.0,
    FAIL: 0.0,
}

#: A section in one of these states cannot be a project's "done" state.
_INCOMPLETE = (FAIL, PARTIAL, NOT_CONFIGURED, STALE)


@dataclass
class StatusSection:
    """One line of the ``forge check`` report."""

    name: str
    state: str
    detail: str = ""
    codes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
            "codes": list(self.codes),
        }


@dataclass
class ProjectStatus:
    """Everything ``forge check`` and ``forge next`` need, computed once.

    Attributes:
        root: Project root, or the directory that was checked when there is
            no project there yet.
        config: The loaded ``forge.yml``, when there is one.
        discovery: The scan the checks ran against, when one was performed.
        sections: One per subject area, in report order.
        report: The diagnostics, as the same ``ATGDiagnosticReport`` every
            other FORGE command emits.
        actions: Recommended next steps, most urgent first.
    """

    root: Path
    config: Optional[ForgeConfig] = None
    paths: Optional[ProjectPaths] = None
    discovery: Optional[DiscoveryResult] = None
    sections: List[StatusSection] = field(default_factory=list)
    report: ATGDiagnosticReport = field(default_factory=lambda: ATGDiagnosticReport("forge check"))
    actions: List[Action] = field(default_factory=list)
    #: ``action.id`` -> the section that asked for it. Lets ``forge next``
    #: explain a recommendation with the finding that produced it rather
    #: than with whichever diagnostic happens to read similarly.
    action_sections: Dict[str, str] = field(default_factory=dict)

    # ── Aggregates ────────────────────────────────────────────────────────

    @property
    def blockers(self) -> List[Any]:
        """Diagnostics that stop the project working at all."""
        return list(self.report.errors)

    @property
    def warnings(self) -> List[Any]:
        return list(self.report.warnings)

    @property
    def status(self) -> str:
        """The envelope status: ``pass`` / ``warn`` / ``fail``."""
        if self.blockers:
            return "fail"
        if self.warnings:
            return "warn"
        return "pass"

    @property
    def completion(self) -> float:
        """Fraction of the project that is fully described, 0.0-1.0."""
        if not self.sections:
            return 0.0
        total = sum(_COMPLETION.get(s.state, 0.0) for s in self.sections)
        return round(total / len(self.sections), 4)

    @property
    def maturity(self) -> str:
        """A word for :attr:`completion`, for report headers."""
        percent = self.completion
        if percent >= 0.999:
            return "complete"
        if percent >= 0.75:
            return "nearly complete"
        if percent >= 0.4:
            return "in progress"
        return "started"

    def section(self, name: str) -> Optional[StatusSection]:
        return next((s for s in self.sections if s.name == name), None)

    def reason_for(self, action: Action) -> str:
        """Why *action* is recommended, in one sentence.

        The diagnostic the action's own section raised, where it raised one;
        otherwise the section's own state, which is the real reason for a
        step like "generate the top level" that no diagnostic reports (there
        is nothing wrong — the work simply has not been done).
        """
        section = self.section(self.action_sections.get(action.id, ""))
        if section is None:
            return ""
        if section.codes:
            for diagnostic in list(self.report.errors) + list(self.report.warnings):
                if diagnostic.code in section.codes:
                    return diagnostic.message
        detail = f" — {section.detail}" if section.detail else ""
        return f"{section.name}: {section.state.lower()}{detail}"

    @property
    def recommended_action(self) -> Optional[Action]:
        """The single next step ``forge next`` reports.

        :attr:`actions` is already ordered by urgency (see
        :func:`_rank_actions`), so this is simply the first of them.
        """
        return self.actions[0] if self.actions else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": str(self.root),
            "config": str(self.config.root / ROOT_CONFIG_NAME) if self.config else None,
            "completion": self.completion,
            "maturity": self.maturity,
            "sections": [s.to_dict() for s in self.sections],
            "blockers": [d.to_dict() for d in self.blockers],
            "warnings": [d.to_dict() for d in self.warnings],
            "actions": [a.to_dict() for a in self.actions],
        }


# ── The check protocol ────────────────────────────────────────────────────

@dataclass
class _CheckOutput:
    """What one check contributes."""

    section: StatusSection
    actions: List[Action] = field(default_factory=list)


CheckFn = Callable[[ProjectStatus], Optional[_CheckOutput]]


def evaluate(
    target: "Path | str",
    *,
    discovery: Optional[DiscoveryResult] = None,
) -> ProjectStatus:
    """Assess the project at (or above) *target*.

    Walks up for a ``forge.yml`` the way ``git`` looks for ``.git``, so the
    command works from anywhere inside the project. When there is no project
    to check, the returned status says exactly that and recommends
    ``forge adopt`` — the one case where a failing check is the expected,
    useful answer rather than a broken project.
    """
    target_path = Path(target).expanduser().resolve()
    root = find_project_root(target_path)

    if root is None:
        return _not_a_project(target_path)

    status = ProjectStatus(root=root)
    try:
        status.config = ForgeConfig.load(root / ROOT_CONFIG_NAME)
    except (ValueError, KeyError, TypeError) as exc:
        status.report.error(
            "ATG030",
            f"{ROOT_CONFIG_NAME} could not be read: {exc}",
            action=f"Fix {ROOT_CONFIG_NAME}, or re-run `forge adopt {root}` to regenerate it",
            path=root / ROOT_CONFIG_NAME,
        )
        status.sections.append(StatusSection(
            "Project configuration", FAIL, str(exc), ("ATG030",)))
        status.actions.append(Action(
            id="fix-project-config",
            description=f"Repair {ROOT_CONFIG_NAME} — every other check depends on it",
            command=f"forge adopt {root}",
        ))
        return status

    status.paths = status.config.paths()
    if status.config.authored:
        checks = AUTHORED_CHECKS
    else:
        checks = CHECKS
        # The same decisions adoption honours: a question the project has
        # already answered in forge.yml must not be re-asked by `forge check`
        # either, or answering it would appear to have done nothing.
        status.discovery = discovery or ProjectDiscovery(
            decisions=status.config.decisions()
        ).scan(root)

    ranked: List[Tuple[int, int, Action]] = []
    for order, check in enumerate(checks):
        output = check(status)
        if output is None:
            continue
        status.sections.append(output.section)
        for action in output.actions:
            ranked.append((_rank_action(output.section, action), order, action))
            status.action_sections.setdefault(action.id, output.section.name)

    status.actions = dedupe([a for _, _, a in sorted(ranked, key=lambda r: (r[0], r[1]))])
    return status


def _rank_action(section: StatusSection, action: Action) -> int:
    """How urgent an action is, lower being more urgent.

    Check order alone is the wrong order to recommend work in: it runs
    contracts before generation because generation depends on contracts, but
    a *draft* contract does not block generation, so recommending a review
    pass ahead of the build leaves a new user polishing YAML for a design
    they have not yet seen generated. So: anything that actually blocks the
    project comes first, then any step that can be performed by running a
    command, then the judgement calls that need a human to sit down with the
    design.
    """
    if section.state == FAIL:
        return 0
    if action.command:
        return 1
    return 2


def _not_a_project(target: Path) -> ProjectStatus:
    """The status of a directory FORGE has never been pointed at."""
    status = ProjectStatus(root=target)
    status.report.error(
        "ATG030",
        f"no {ROOT_CONFIG_NAME} in {target} or any parent directory — "
        f"this directory is not a FORGE project yet",
        action=f"Run `forge adopt {target}` to scan it and create one",
        path=target,
    )
    status.sections.append(StatusSection(
        "Project configuration", NOT_CONFIGURED,
        f"no {ROOT_CONFIG_NAME} found", ("ATG030",),
    ))
    status.actions.append(Action(
        id="adopt-project",
        description="Scan this repository and create a FORGE project from what is in it",
        command=f"forge adopt {target}",
    ))
    return status


# ── Individual checks ─────────────────────────────────────────────────────

def _check_sources(status: ProjectStatus) -> _CheckOutput:
    """Do the configured source globs still match real files?

    A glob that matches nothing is the single most common way an adopted
    project breaks: someone moves ``rtl/`` and every downstream command
    fails with a different, less useful message.
    """
    config, discovery = status.config, status.discovery
    assert config is not None and discovery is not None

    rtl = config.resolve_sources("rtl")
    hls = config.resolve_sources("hls")
    if not rtl and not hls:
        status.report.error(
            "ATG031",
            f"no source files match the globs in {ROOT_CONFIG_NAME} "
            f"({', '.join(config.rtl_globs + config.hls_globs) or 'none configured'})",
            action="Correct sources.rtl/sources.hls in forge.yml, or re-run `forge adopt`",
            path=config.root / ROOT_CONFIG_NAME,
        )
        return _CheckOutput(
            StatusSection("Sources", FAIL, "no files matched", ("ATG031",)),
            [Action(
                id="fix-source-globs",
                description="Point sources.rtl in forge.yml at the design's real source directories",
                command=f"forge adopt {config.root}",
            )],
        )

    duplicates = _duplicate_modules(discovery)
    for name, files in duplicates.items():
        status.report.error(
            "ATG032",
            f"module {name!r} is declared in {len(files)} files: "
            f"{', '.join(sorted(str(f) for f in files))}",
            action=f"Remove or rename the duplicate declaration of {name!r}",
            module=name,
        )

    for unit in discovery.units:
        if not unit.path.is_file():
            status.report.error(
                "ATG033",
                f"module {unit.name!r} was discovered in {unit.path}, which no longer exists",
                action=f"Restore the file, or re-run `forge adopt {config.root}`",
                path=unit.path,
                module=unit.name,
            )

    detail = f"{len(rtl)} RTL, {len(hls)} HLS, {len(discovery.units)} modules"
    if duplicates:
        return _CheckOutput(
            StatusSection("Sources", FAIL, f"{detail}; {len(duplicates)} duplicated", ("ATG032",)),
            [Action(
                id="resolve-duplicate-module",
                description=(
                    f"Remove the duplicate declaration of "
                    f"{', '.join(sorted(duplicates))} — FORGE cannot tell which is the real one"
                ),
                safe=False,
            )],
        )
    return _CheckOutput(StatusSection("Sources", PASS, detail))


def _duplicate_modules(discovery: DiscoveryResult) -> Dict[str, List[Path]]:
    """Module names declared in more than one discovered file.

    Discovery keeps only the first of a duplicated name, so this re-reads
    the files rather than the unit list — the point is to report the
    collision, which the deduplicated list has by definition lost.
    """
    from forge.project.hdl_scan import scan_hdl_file

    seen: Dict[str, List[Path]] = {}
    for discovered in discovery.files_by_role("rtl"):
        for unit in scan_hdl_file(discovered.path):
            seen.setdefault(unit.name, []).append(discovered.path)
    return {name: files for name, files in seen.items() if len(files) > 1}


def _check_contracts(status: ProjectStatus) -> _CheckOutput:
    """Does every managed module have a contract that matches its source?

    A module's contract is inferred from its ports on every command, so it
    can only disagree with its source when ``forge.yml`` names a contract
    of the user's own. Those are checked against the source; the inferred
    ones need nothing from the user.
    """
    config, paths, discovery = status.config, status.paths, status.discovery
    assert config is not None and paths is not None and discovery is not None

    managed = discovery.managed_units
    if not managed:
        return _CheckOutput(StatusSection(
            "Contracts", NOT_CONFIGURED, "no managed modules"))

    missing: List[str] = []
    drifted: List[str] = []
    own = 0
    for unit in managed:
        policy = config.modules.get(unit.name)
        if policy is None or not policy.contract:
            continue
        own += 1
        contract = (config.root / policy.contract).resolve()
        if not contract.is_file():
            missing.append(unit.name)
            status.report.error(
                "ATG034",
                f"forge.yml names {policy.contract} as the contract for module "
                f"{unit.name!r}, and it does not exist",
                action=(
                    f"Correct modules.{unit.name}.contract in {ROOT_CONFIG_NAME}, or remove "
                    f"it to use the contract inferred from the ports"
                ),
                path=contract,
                module=unit.name,
            )
            continue
        for drift in _contract_drift(contract, unit):
            drifted.append(unit.name)
            status.report.error(
                "ATG036",
                f"contract for {unit.name!r} disagrees with {unit.path.name}: {drift}",
                action=f"Correct {paths.relative(contract)} to match the module's ports",
                path=contract,
                module=unit.name,
            )

    if drifted:
        return _CheckOutput(
            StatusSection(
                "Contracts", FAIL,
                f"{len(set(drifted))} disagree with their source", ("ATG036",),
            ),
            [Action(
                id="resolve-contract-drift",
                description=(
                    f"Reconcile the contract(s) for {', '.join(sorted(set(drifted)))} with "
                    f"the module source they describe"
                ),
                auto_fixable=True,
            )],
        )
    if missing:
        return _CheckOutput(
            StatusSection("Contracts", FAIL, f"{len(missing)} missing", ("ATG034",)),
            [Action(
                id="fix-contract-paths",
                description=(
                    f"Point modules.<name>.contract in {ROOT_CONFIG_NAME} at existing files "
                    f"for {', '.join(missing)}"
                ),
                safe=False,
            )],
        )
    detail = f"{len(managed) - own} inferred from source"
    if own:
        detail += f", {own} from {ROOT_CONFIG_NAME}"
    return _CheckOutput(StatusSection("Contracts", PASS, detail))


def _contract_drift(contract: Path, unit) -> List[str]:
    """Ways *contract* contradicts the module source it claims to describe.

    Only the facts a contract may restate are compared — the ``raw_port`` a
    role binds to, and the width where the contract states one. A contract
    that names a port the module does not have will fail at generation time
    with a far less useful message; catching it here is the difference
    between "your contract is out of date" and a template error deep inside
    a generator.

    Slim contracts (the form ``forge adopt`` writes, where the role name
    *is* the port name and nothing is restated) have nothing to drift, so
    this reports nothing for them — which is exactly why the slim form is
    the one adoption generates.
    """
    import yaml

    try:
        document = yaml.safe_load(contract.read_text()) or {}
    except Exception:  # noqa: BLE001 — a broken contract is ATG015's finding
        return []
    roles = ((document.get("ip_interface") or {}).get("roles") or {})
    if not isinstance(roles, dict):
        return []

    problems: List[str] = []
    for role_name, role in roles.items():
        body = role if isinstance(role, dict) else {}
        port_name = str(body.get("raw_port") or role_name)
        port = unit.ports.get(port_name)
        if port is None:
            problems.append(f"role {role_name!r} binds to port {port_name!r}, which does not exist")
            continue
        declared_width = body.get("width")
        if declared_width is not None and int(declared_width) != port.width:
            problems.append(
                f"role {role_name!r} declares width {declared_width}, "
                f"source declares {port.width}"
            )
    return problems


def _check_topology(status: ProjectStatus) -> _CheckOutput:
    """Does every producer have exactly one determined consumer?

    This is the check the plan's worked example is about. An ambiguous
    consumer is a blocker precisely *because* FORGE could pick one: both
    candidates satisfy width and direction, so a guess would generate a
    design that builds, simulates, and is wrong.
    """
    discovery = status.discovery
    assert discovery is not None

    ambiguous = [
        a for a in discovery.ambiguities_of(KIND_CONNECTION, KIND_TOP, KIND_HLS_TOP)
        if a.classification == AMBIGUOUS
    ]
    for ambiguity in ambiguous:
        status.report.error(
            "ATG037",
            f"{ambiguity.question}",
            action=ambiguity.action.description if ambiguity.action else "",
            module=ambiguity.subject.split(".")[0],
            candidates=list(ambiguity.options),
        )

    connections = len(discovery.connections)
    if ambiguous:
        return _CheckOutput(
            StatusSection(
                "Topology", FAIL,
                f"{connections} resolved, {len(ambiguous)} undecided", ("ATG037",),
            ),
            [a.action for a in ambiguous if a.action],
        )
    if not connections and len(discovery.managed_units) > 1:
        status.report.warn(
            "ATG038",
            f"{len(discovery.managed_units)} modules and no connection between any of "
            f"them — every port is exposed at the top level",
            action=(
                "Add the intended producer/consumer pairs to connections: in "
                ".forge/project/design.yml if the modules are meant to feed each other"
            ),
        )
        return _CheckOutput(
            StatusSection("Topology", PARTIAL, "no internal connections", ("ATG038",)),
            [Action(
                id="declare-connections",
                description=(
                    "Declare how the modules connect — FORGE found no port pair whose "
                    "names and widths agree, so it wired none of them"
                ),
                safe=False,
            )],
        )
    return _CheckOutput(StatusSection(
        "Topology", PASS,
        f"{connections} connections, {len(discovery.external_inputs)} in / "
        f"{len(discovery.external_outputs)} out",
    ))


def _check_clock_reset(status: ProjectStatus) -> _CheckOutput:
    """Is there one clock and one reset, and can FORGE express them?"""
    config, discovery = status.config, status.discovery
    assert config is not None and discovery is not None

    codes: List[str] = []
    actions: List[Action] = []
    state = PASS
    details: List[str] = []

    clocks = discovery.managed_clocks
    if not clocks:
        status.report.error(
            "ATG039",
            "no clock port found on any module — FORGE connects a clock to every "
            "managed instance and cannot identify one",
            action="Name the clock port in forge.yml under clock.port",
        )
        codes.append("ATG039")
        state = FAIL
        actions.append(Action(
            id="declare-clock",
            description="Name the design's clock port in forge.yml",
            safe=False,
        ))
    elif config.clock.port not in {c.name for c in clocks}:
        names = ", ".join(c.name for c in clocks)
        status.report.error(
            "ATG039",
            f"forge.yml declares {config.clock.port!r} as the clock, but no module has "
            f"a port by that name — the ports that look like clocks are: {names}",
            action=f"Set clock.port in forge.yml to one of: {names}",
        )
        codes.append("ATG039")
        state = FAIL
        actions.append(Action(
            id="declare-clock",
            description=(
                f"Set clock.port in forge.yml to the design's functional clock "
                f"(candidates: {names})"
            ),
            safe=False,
        ))
    elif len(clocks) > 1:
        names = ", ".join(c.name for c in clocks)
        status.report.warn(
            "ATG040",
            f"{len(clocks)} distinct clock port names found on the managed modules "
            f"({names}); "
            f"forge.yml declares {config.clock.port!r} as the functional clock — "
            f"confirm that is the one you meant",
            action=(
                "Confirm clock.port in forge.yml, and import any module on a different "
                "clock as opaque — FORGE manages one functional clock domain per module"
            ),
        )
        codes.append("ATG040")
        state = WARN
        details.append(f"{len(clocks)} clock names")
        actions.append(Action(
            id="confirm-clock",
            description=(
                f"Confirm clock.port in forge.yml is the functional clock "
                f"(candidates: {names})"
            ),
            safe=False,
        ))
    else:
        details.append(f"clock {clocks[0].name}")

    if config.reset.active_low:
        details.append(f"reset {config.reset.port} (active low, driven by ~ap_rst)")
    elif discovery.managed_resets:
        details.append(f"reset {config.reset.port}")

    return _CheckOutput(
        StatusSection("Clock/reset model", state, ", ".join(details), tuple(codes)),
        actions,
    )


def _check_unsupported(status: ProjectStatus) -> Optional[_CheckOutput]:
    """Modules at the edge of FORGE's envelope, and how they are handled.

    Two different states share this section, and the difference is the whole
    point of opaque modules: a module FORGE cannot model *and nobody has
    decided about* is an open question that leaves it out of the design; one
    the user has declared opaque is a decision already made, and the module
    is in the design.

    Returns ``None`` — no section at all — when neither applies, so an
    ordinary project's report carries no line about a limitation it never
    meets.
    """
    discovery = status.discovery
    assert discovery is not None
    opaque = discovery.opaque_units
    if not discovery.unsupported and not opaque:
        return None

    for unit in opaque:
        status.report.note(
            "ATG047",
            f"{unit.name} is integrated as an opaque module — FORGE wires it "
            f"structurally and does not model its internals"
            + (f" ({'; '.join(unit.unsupported)})" if unit.unsupported else ""),
            action=(
                "Its clock/reset pins other than the design's own are exposed at the "
                "generated top level for the enclosing design to drive"
            ),
            module=unit.name,
        )

    for ambiguity in discovery.unsupported:
        status.report.warn(
            "ATG042",
            ambiguity.question,
            action="; ".join(ambiguity.options),
            module=ambiguity.subject,
        )

    if not discovery.unsupported:
        names = sorted(unit.name for unit in opaque)
        return _CheckOutput(StatusSection(
            "Supported constructs", PASS,
            f"{len(names)} opaque: {', '.join(names)}", ("ATG047",),
        ))

    left_out = sorted({a.subject for a in discovery.unsupported})
    detail = f"{len(left_out)} module(s) left out: {', '.join(left_out)}"
    if opaque:
        detail += f"; {len(opaque)} opaque"
    return _CheckOutput(
        StatusSection("Supported constructs", PARTIAL, detail, ("ATG042",)),
        [a.action for a in discovery.unsupported if a.action],
    )


def _check_hls(status: ProjectStatus) -> Optional[_CheckOutput]:
    """HLS kernels found, and how far their interfaces can be trusted.

    An HLS module's real RTL ports are only known after synthesis. Until
    then a contract is a prediction, and reporting it as anything else would
    make the project look more finished than it is.
    """
    discovery = status.discovery
    assert discovery is not None
    if not discovery.hls_candidates:
        return None

    from forge.project.hls_maturity import RECONCILED, assess_candidate, is_at_least

    assessments = [
        assess_candidate(candidate, root=status.root)
        for candidate in discovery.hls_candidates
    ]
    reconciled = [a for a in assessments if is_at_least(a.level, RECONCILED)]
    unreconciled = [a for a in assessments if not is_at_least(a.level, RECONCILED)]

    for assessment in assessments:
        if is_at_least(assessment.level, RECONCILED):
            differences = (
                assessment.reconciliation.differences
                if assessment.reconciliation else []
            )
            if not differences:
                continue
            status.report.warn(
                "ATG048",
                f"{assessment.module}: the predicted RTL interface and the built IP "
                f"differ in {len(differences)} place(s) — "
                f"{'; '.join(d.describe() for d in differences[:3])}"
                + (" …" if len(differences) > 3 else ""),
                action=(
                    f"Run `forge explain hls:{assessment.module}` for the full "
                    f"reconciliation, then correct the contract to match the built IP"
                ),
                module=assessment.module,
            )
            continue
        status.report.warn(
            "ATG043",
            f"{assessment.module}: {assessment.description} "
            f"(maturity: {assessment.level})",
            action=(
                "Run `forge dev hls run --stages csim,synth` so the real RTL ports are "
                "known, then re-check to reconcile them against the prediction"
            ),
            module=assessment.module,
        )

    if not unreconciled:
        drifted = sum(
            len(a.reconciliation.differences) for a in reconciled if a.reconciliation
        )
        return _CheckOutput(StatusSection(
            "HLS interfaces",
            WARN if drifted else PASS,
            f"{len(reconciled)} reconciled"
            + (f", {drifted} difference(s)" if drifted else ""),
            ("ATG048",) if drifted else (),
        ))

    return _CheckOutput(
        StatusSection(
            "HLS interfaces", PARTIAL,
            f"{len(reconciled)} reconciled, {len(unreconciled)} predicted only",
            ("ATG043",),
        ),
        [Action(
            id="reconcile-hls-interfaces",
            description=(
                "Synthesise the HLS kernels so their real RTL ports are known and "
                "can be reconciled against the prediction"
            ),
            command="forge dev hls run --stages csim,synth",
        )],
    )


def _flows_without_simulator(design: Path) -> List[Tuple[str, List[str]]]:
    """``(flow, missing tools)`` for each flow whose simulator is not on PATH.

    A contract that does not load says nothing here; `forge test` reports it.
    """
    from forge.core.toolchain_versions import tool_present
    from forge.verification.backend_registry import get_adapter
    from forge.verification.design_contract import load_verify_design

    try:
        flows = load_verify_design(design).flows
    except Exception:  # noqa: BLE001
        return []
    unrunnable = []
    for flow in flows:
        try:
            required = get_adapter(flow.backend).required_tools
        except Exception:  # noqa: BLE001 — an unknown backend fails in forge test itself
            continue
        missing = [tool for tool in required if not tool_present(tool)]
        if missing:
            unrunnable.append((flow.name, missing))
    return unrunnable


def _check_verification(status: ProjectStatus) -> _CheckOutput:
    """Is there anything to test the design against?"""
    config, paths = status.config, status.paths
    assert config is not None and paths is not None

    if config.verification_config:
        design = (config.root / config.verification_config).resolve()
        if design.is_file():
            unrunnable = _flows_without_simulator(design)
            if unrunnable:
                status.report.warn(
                    "ATG049",
                    f"{len(unrunnable)} verification flow(s) cannot run here: "
                    + "; ".join(f"{flow} needs {', '.join(tools)}" for flow, tools in unrunnable)
                    + " — `forge test` skips them",
                    action="Put the simulator on PATH (`forge doctor` lists what it finds), "
                           "or run `forge test` where it is installed",
                )
                return _CheckOutput(StatusSection(
                    "Verification", WARN,
                    f"{paths.relative(design)}, {len(unrunnable)} flow(s) without their simulator",
                    ("ATG049",),
                ))
            return _CheckOutput(StatusSection(
                "Verification", PASS, paths.relative(design)))
        status.report.error(
            "ATG044",
            f"verification config {config.verification_config} does not exist",
            action=f"Correct verification.config in {ROOT_CONFIG_NAME}",
            path=design,
        )
        return _CheckOutput(
            StatusSection("Verification", FAIL, "verification config missing", ("ATG044",)),
            [Action(
                id="fix-verification-config",
                description=(
                    f"Point verification.config at a real design.verification.yml "
                    f"(it names {config.verification_config})"
                ),
                safe=False,
            )],
        )

    if config.dataset:
        dataset = (config.root / config.dataset).resolve()
        if not dataset.is_file():
            status.report.error(
                "ATG044",
                f"verification dataset {config.dataset} does not exist",
                action="Correct verification.dataset in forge.yml",
                path=dataset,
            )
            return _CheckOutput(
                StatusSection("Verification", FAIL, "dataset missing", ("ATG044",)),
                [Action(
                    id="fix-verification-dataset",
                    description=f"Point verification.dataset at a real file (it names {config.dataset})",
                    safe=False,
                )],
            )
        return _CheckOutput(
            StatusSection("Verification", PARTIAL, f"dataset {config.dataset}, no flow yet"),
            [Action(
                id="create-verification-flow",
                description=(
                    "Create a verification flow for the dataset, then name its "
                    "design.verification.yml as verification.config in forge.yml — a "
                    "dataset with no flow to consume it does not test anything yet"
                ),
                command=f"forge dev verify init-plugin {config.project}",
            )],
        )

    status.report.warn(
        "ATG044",
        "no verification is configured for this design — nothing checks that the "
        "generated top level behaves correctly",
        action=(
            "Set verification.config in forge.yml to a design.verification.yml, "
            "then run `forge test`"
        ),
    )
    return _CheckOutput(
        StatusSection("Verification", NOT_CONFIGURED, "no verification config", ("ATG044",)),
        [Action(
            id="configure-verification",
            description=(
                "Configure verification — set verification.config in forge.yml to the "
                "design.verification.yml that tests this design"
            ),
            safe=False,
        )],
    )


def _check_latency(status: ProjectStatus) -> _CheckOutput:
    """Do the managed modules declare their latency?

    FORGE's static latency check can only align pipelines it knows the
    depth of, and a module's latency is not visible in its port list — so
    adoption never invents one, and this reports the gap rather than
    letting the latency report be quietly meaningless.
    """
    config, discovery = status.config, status.discovery
    assert config is not None and discovery is not None

    managed = discovery.managed_units
    if not managed:
        return _CheckOutput(StatusSection("Latency model", NOT_CONFIGURED, "no modules"))

    undeclared = [
        unit.name for unit in managed
        if (config.modules.get(unit.name) or ModulePolicy()).latency is None
    ]
    declared = len(managed) - len(undeclared)
    if not undeclared:
        return _CheckOutput(StatusSection(
            "Latency model", PASS, f"{declared} of {len(managed)} declared"))

    status.report.warn(
        "ATG045",
        f"{len(undeclared)} of {len(managed)} modules declare no latency — "
        f"the static latency check cannot align a pipeline whose stage depths are unknown",
        action=(
            f"Add `latency: <cycles>` under modules.<name> in {ROOT_CONFIG_NAME} for "
            f"{', '.join(undeclared)}"
        ),
        path=config.root / ROOT_CONFIG_NAME,
    )
    return _CheckOutput(
        StatusSection(
            "Latency model", PARTIAL,
            f"{declared} of {len(managed)} declared", ("ATG045",),
        ),
        [Action(
            id="declare-latency",
            description=(
                f"Declare each module's latency in {ROOT_CONFIG_NAME} "
                f"(`modules: {{{undeclared[0]}: {{latency: <cycles>}}}}`) — it cannot be "
                f"read from the ports"
            ),
            safe=False,
        )],
    )


def _changed_by_content(top: Path) -> Optional[List[str]]:
    """Inputs of *top*'s build whose content changed since, per its provenance; ``None`` without one."""
    import json

    from forge.ir.provenance import changed_sources

    manifest, provenance = top.parent / "build_manifest.json", top.parent / "provenance.json"
    if not (manifest.is_file() and provenance.is_file()):
        return None
    try:
        design_dir = Path(json.loads(manifest.read_text())["design_file"]).parent
    except (OSError, ValueError, KeyError):
        return None
    return changed_sources(provenance, design_dir)


def _check_generated(status: ProjectStatus) -> _CheckOutput:
    """Has the design been generated, and is what was generated current?

    Staleness is decided by content: the provenance written with the top
    level records a hash of every input, and an input whose content differs
    makes the build stale whatever its timestamp says (a fresh clone or a
    restored cache has misleading times). Without a provenance record the
    newest input timestamp is compared with the top level's.
    """
    config, paths = status.config, status.paths
    assert config is not None and paths is not None

    top = paths.top_level(
        config.build.get("mode", "verilog"), config.build.get("top_name", "algo_top"),
    )
    if not top.is_file():
        return _CheckOutput(
            StatusSection("Generated artifacts", NOT_CONFIGURED, "not generated yet"),
            [Action(
                id="build-design",
                description="Generate the structural top level",
                command="forge build",
            )],
        )

    by_content = _changed_by_content(top)
    if by_content is not None:
        if by_content:
            status.report.warn(
                "ATG046",
                f"{paths.relative(top)} was built from other contents of {len(by_content)} of its inputs "
                f"({', '.join(by_content[:3])}{', …' if len(by_content) > 3 else ''})",
                action="Re-run `forge build` to regenerate from the current sources",
                path=top,
            )
            return _CheckOutput(
                StatusSection("Generated artifacts", STALE, f"{len(by_content)} inputs changed", ("ATG046",)),
                [Action(
                    id="rebuild-design",
                    description="Regenerate the top level — its inputs have changed since it was built",
                    command="forge build",
                    auto_fixable=True,
                )],
            )
        return _CheckOutput(StatusSection("Generated artifacts", PASS, paths.relative(top)))

    generated_at = top.stat().st_mtime
    if config.authored:
        from forge.contracts.design_variant import design_sources

        design, registry = _authored_files(config)
        candidates = [config.root / ROOT_CONFIG_NAME] + design_sources(design)
        if registry is not None:
            candidates.append(registry)
    else:
        own_contracts = [
            (config.root / policy.contract).resolve()
            for policy in config.modules.values() if policy.contract
        ]
        candidates = (
            [config.root / ROOT_CONFIG_NAME] + own_contracts
            + config.resolve_sources("rtl") + config.resolve_sources("hls")
        )
    inputs = [p for p in candidates if p.is_file()]
    newer = [p for p in inputs if p.stat().st_mtime > generated_at]
    if newer:
        status.report.warn(
            "ATG046",
            f"{paths.relative(top)} is older than {len(newer)} of its inputs "
            f"({', '.join(paths.relative(p) for p in newer[:3])}"
            f"{', …' if len(newer) > 3 else ''})",
            action="Re-run `forge build` to regenerate from the current sources",
            path=top,
        )
        return _CheckOutput(
            StatusSection("Generated artifacts", STALE, f"{len(newer)} inputs newer", ("ATG046",)),
            [Action(
                id="rebuild-design",
                description="Regenerate the top level — its inputs have changed since it was built",
                command="forge build",
                auto_fixable=True,
            )],
        )
    return _CheckOutput(StatusSection(
        "Generated artifacts", PASS, paths.relative(top)))


# ── A design named in forge.yml ───────────────────────────────────────────

def _authored_files(config: ForgeConfig) -> Tuple[Path, Optional[Path]]:
    """The design ``forge.yml`` names and the registry that goes with it."""
    from forge.project.resolve import design_registry

    assert config.design is not None
    design = (config.root / config.design).resolve()
    if config.registry:
        return design, (config.root / config.registry).resolve()
    return design, design_registry(design) if design.is_file() else None


def _check_design(status: ProjectStatus) -> _CheckOutput:
    """Do the design and registry ``forge.yml`` names exist and validate?"""
    from forge.contracts.config import DesignConfig
    from forge.generation.validation import validate_design, validate_registry

    config, paths = status.config, status.paths
    assert config is not None and paths is not None
    design, registry = _authored_files(config)

    if not design.is_file():
        status.report.error(
            "ATG001",
            f"{ROOT_CONFIG_NAME} names design {config.design}, which does not exist",
            action=f"Correct design: in {ROOT_CONFIG_NAME}",
            path=design,
        )
        return _CheckOutput(
            StatusSection("Design", FAIL, "design missing", ("ATG001",)),
            [Action(id="fix-design-path", description=f"Point design: in {ROOT_CONFIG_NAME} at a design.yml", safe=False)],
        )
    if registry is None or not registry.is_file():
        named = paths.relative(registry) if registry else "none"
        status.report.error(
            "ATG008",
            f"the design's module registry is missing ({named})",
            action=f"Set registry: in {ROOT_CONFIG_NAME}, or the design's own registry: key",
            path=registry or design,
        )
        return _CheckOutput(
            StatusSection("Design", FAIL, "registry missing", ("ATG008",)),
            [Action(id="fix-registry-path", description="Name the design's modules.yml", safe=False)],
        )

    try:
        cfg = DesignConfig.load_relaxed(design)
    except Exception as exc:  # noqa: BLE001 — the user's file is wrong, not FORGE
        status.report.error(
            "ATG015", f"{paths.relative(design)} could not be loaded: {exc}",
            action=f"Fix {paths.relative(design)}", path=design,
        )
        return _CheckOutput(
            StatusSection("Design", FAIL, "does not load", ("ATG015",)),
            [Action(id="fix-design", description=f"Fix {paths.relative(design)}", safe=False)],
        )

    problems = list(validate_design(cfg, design).errors) + list(validate_registry(registry).errors)
    for problem in problems:
        status.report.error(
            "ATG015", f"[{problem.category}] {problem.message}",
            action=problem.suggestion or f"Fix {paths.relative(design)}",
            path=design,
        )
    if problems:
        return _CheckOutput(
            StatusSection("Design", FAIL, f"{len(problems)} validation error(s)", ("ATG015",)),
            [Action(
                id="fix-design",
                description=f"Fix the validation errors in {paths.relative(design)} and its registry",
                safe=False,
            )],
        )
    return _CheckOutput(StatusSection(
        "Design", PASS,
        f"{paths.relative(design)}, {len(cfg.modules)} modules, {len(cfg.connections)} connections",
    ))


def _check_design_latency(status: ProjectStatus) -> Optional[_CheckOutput]:
    """Does the latency graph know every module's depth?

    Asks the same graph ``forge dev analyze latency-check`` builds, so a module
    whose latency comes from its HLS reports (under ``build.hls_build_root``)
    counts as known, exactly as it does there.
    """
    from forge.analysis.latency_static.graph import build_resolved_graph

    config, paths = status.config, status.paths
    assert config is not None and paths is not None
    design, registry = _authored_files(config)
    if registry is None or not design.is_file() or not registry.is_file():
        return None

    hls_root = config.build.get("hls_build_root")
    try:
        graph = build_resolved_graph(
            design, modules_yml_path=registry,
            hls_build_root=config.root / hls_root if hls_root else None,
        )
    except Exception:  # noqa: BLE001 — a design that does not load is _check_design's finding
        return None

    modules = sorted({node.module for node in graph.nodes.values()})
    unknown = sorted({node.module for node in graph.nodes.values() if node.latency is None})
    known = len(modules) - len(unknown)
    if not unknown:
        return _CheckOutput(StatusSection(
            "Latency model", PASS, f"{known} of {len(modules)} known"))

    status.report.warn(
        "ATG045",
        f"{len(unknown)} of {len(modules)} modules have no known latency "
        f"({', '.join(unknown)}) — the static latency check cannot align a pipeline "
        f"whose stage depths are unknown",
        action=(
            f"Declare `latency:` for them in {paths.relative(registry)}, or point "
            f"build.hls_build_root in {ROOT_CONFIG_NAME} at their HLS reports"
        ),
        path=registry,
    )
    return _CheckOutput(
        StatusSection(
            "Latency model", PARTIAL, f"{known} of {len(modules)} known", ("ATG045",),
        ),
        [Action(
            id="declare-latency",
            description=f"Declare the missing latencies in {paths.relative(registry)}",
            safe=False,
        )],
    )


#: Run in dependency order — a project with no sources has nothing to say
#: about its topology, and ``forge next`` reports the first action recorded,
#: so this order *is* the recommended order of work.
CHECKS: Tuple[CheckFn, ...] = (
    _check_sources,
    _check_contracts,
    _check_topology,
    _check_clock_reset,
    _check_unsupported,
    _check_hls,
    _check_latency,
    _check_generated,
    _check_verification,
)

#: The checks for a project whose forge.yml names its own design.
AUTHORED_CHECKS: Tuple[CheckFn, ...] = (
    _check_design,
    _check_design_latency,
    _check_generated,
    _check_verification,
)
