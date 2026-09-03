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
from forge.project.config import ForgeConfig
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
    # The same decisions adoption honours: a question the project has
    # already answered in forge.yml must not be re-asked by `forge check`
    # either, or answering it would appear to have done nothing.
    status.discovery = discovery or ProjectDiscovery(
        decisions=status.config.decisions()
    ).scan(root)

    ranked: List[Tuple[int, int, Action]] = []
    for order, check in enumerate(CHECKS):
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
    """Is there a reviewed interface contract for every managed module?

    A missing contract blocks generation. A ``draft`` one does not block it
    but means nobody has confirmed the semantics FORGE could not infer, so
    it is reported as partial rather than passing — the difference matters
    to anyone about to trust the generated wiring.
    """
    paths, discovery = status.paths, status.discovery
    assert paths is not None and discovery is not None

    managed = discovery.managed_units
    if not managed:
        return _CheckOutput(StatusSection(
            "Contracts", NOT_CONFIGURED, "no managed modules"))

    missing: List[str] = []
    draft: List[str] = []
    drifted: List[str] = []
    for unit in managed:
        contract = paths.contract_root / f"{unit.name}.interface.yaml"
        if not contract.is_file():
            missing.append(unit.name)
            status.report.error(
                "ATG034",
                f"no interface contract for module {unit.name!r} "
                f"(expected {contract})",
                action=f"Run `forge adopt {paths.root}` to generate it, or write it by hand",
                path=contract,
                module=unit.name,
            )
            continue
        text = contract.read_text()
        if "normalization_status: draft" in text:
            draft.append(unit.name)
            status.report.warn(
                "ATG035",
                f"contract for {unit.name!r} is still a draft — the semantics FORGE "
                f"could not infer (wiring_kind, protocol, coordinates) have not been "
                f"reviewed",
                action=(
                    f"Review {contract.name} and set normalization_status: ready "
                    f"once its wiring semantics are correct"
                ),
                path=contract,
                module=unit.name,
            )
        for drift in _contract_drift(contract, unit):
            drifted.append(unit.name)
            status.report.error(
                "ATG036",
                f"contract for {unit.name!r} disagrees with {unit.path.name}: {drift}",
                action=(
                    f"Correct {contract.name}, or re-run `forge adopt {paths.root}` to "
                    f"regenerate it from the current source"
                ),
                path=contract,
                module=unit.name,
            )

    actions: List[Action] = []
    if drifted:
        actions.append(Action(
            id="resolve-contract-drift",
            description=(
                f"Reconcile the contract(s) for {', '.join(sorted(set(drifted)))} with "
                f"the module source they describe"
            ),
            command=f"forge adopt {paths.root}",
            auto_fixable=True,
        ))
        return _CheckOutput(
            StatusSection(
                "Contracts", FAIL,
                f"{len(set(drifted))} disagree with their source", ("ATG036",),
            ),
            actions,
        )
    if missing:
        actions.append(Action(
            id="generate-missing-contracts",
            description=f"Generate the missing contract(s) for {', '.join(missing)}",
            command=f"forge adopt {paths.root}",
            auto_fixable=True,
        ))
        return _CheckOutput(
            StatusSection("Contracts", FAIL, f"{len(missing)} missing", ("ATG034",)), actions)
    if draft:
        actions.append(Action(
            id="review-draft-contracts",
            description=(
                f"Review the {len(draft)} draft contract(s) under "
                f"{paths.relative(paths.contract_root)} and mark them ready"
            ),
            safe=False,
        ))
        return _CheckOutput(
            StatusSection(
                "Contracts", PARTIAL, f"{len(draft)} of {len(managed)} unreviewed", ("ATG035",),
            ),
            actions,
        )
    return _CheckOutput(StatusSection("Contracts", PASS, f"{len(managed)} reviewed"))


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
    """Is there one clock and one reset, and can FORGE express them?

    The active-low warning is the honest one: FORGE's generators collapse
    every reset onto a single top-level ``ap_rst`` net and do not model its
    polarity, so an active-low module reset is wired to it unchanged. That
    is a real limitation, and the plan's rule for limitations is that the
    user learns about them early rather than from a waveform.
    """
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
        status.report.warn(
            "ATG041",
            f"reset {config.reset.port!r} is active-low, and FORGE's generators wire "
            f"every module reset to one top-level active-high ap_rst net without "
            f"inverting it",
            action=(
                "Drive ap_rst with the polarity your modules expect at the level above "
                "the generated top, or invert inside the modules"
            ),
        )
        codes.append("ATG041")
        state = WARN if state == PASS else state
        details.append(f"reset {config.reset.port} (active low)")
        actions.append(Action(
            id="confirm-reset-polarity",
            description=(
                f"Confirm how {config.reset.port!r} is driven — FORGE does not model "
                f"reset polarity and wires it straight through"
            ),
            safe=False,
        ))
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
                "Run `forge hls run --stages csim,synth` so the real RTL ports are "
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
            command="forge hls run --stages csim,synth",
        )],
    )


def _check_verification(status: ProjectStatus) -> _CheckOutput:
    """Is there anything to test the design against?"""
    config, paths = status.config, status.paths
    assert config is not None and paths is not None

    if paths.verification_yml.is_file():
        return _CheckOutput(StatusSection(
            "Verification", PASS, paths.relative(paths.verification_yml)))

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
                    "Create a verification flow for the dataset — a dataset with no "
                    "flow to consume it does not test anything yet"
                ),
                command=f"forge verify init-plugin {config.project}",
            )],
        )

    status.report.warn(
        "ATG044",
        "no verification is configured for this design — nothing checks that the "
        "generated top level behaves correctly",
        action="Add verification.dataset to forge.yml, then run `forge test prepare`",
    )
    return _CheckOutput(
        StatusSection("Verification", NOT_CONFIGURED, "no dataset, no flow", ("ATG044",)),
        [Action(
            id="configure-verification",
            description=(
                "Configure verification — add verification.dataset to forge.yml "
                "pointing at the stimulus/expected-output data for this design"
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
    import yaml

    paths, discovery = status.paths, status.discovery
    assert paths is not None and discovery is not None

    managed = discovery.managed_units
    if not managed:
        return _CheckOutput(StatusSection("Latency model", NOT_CONFIGURED, "no modules"))
    if not paths.modules_yml.is_file():
        return _CheckOutput(StatusSection(
            "Latency model", NOT_CONFIGURED, "no module registry"))

    registry = yaml.safe_load(paths.modules_yml.read_text()) or {}
    entries = registry.get("modules") or []
    declared = [
        e for e in entries
        if e.get("latency") is not None or e.get("latency_hint") is not None
    ]
    if len(declared) == len(entries) and entries:
        return _CheckOutput(StatusSection(
            "Latency model", PASS, f"{len(declared)} of {len(entries)} declared"))

    status.report.warn(
        "ATG045",
        f"{len(entries) - len(declared)} of {len(entries)} modules declare no latency — "
        f"the static latency check cannot align a pipeline whose stage depths are unknown",
        action=(
            f"Add `latency: {{kind: fixed, cycles: N}}` to each module in "
            f"{paths.relative(paths.modules_yml)}"
        ),
        path=paths.modules_yml,
    )
    return _CheckOutput(
        StatusSection(
            "Latency model", PARTIAL,
            f"{len(declared)} of {len(entries)} declared", ("ATG045",),
        ),
        [Action(
            id="declare-latency",
            description=(
                "Declare each module's latency in "
                f"{paths.relative(paths.modules_yml)} — it cannot be read from the ports"
            ),
            safe=False,
        )],
    )


def _check_generated(status: ProjectStatus) -> _CheckOutput:
    """Has the design been generated, and is what was generated current?

    Staleness is decided by comparing the newest source/config timestamp
    against the generated top level: reporting a build as done when its
    inputs have since changed is the failure mode this check exists for.
    """
    config, paths = status.config, status.paths
    assert config is not None and paths is not None

    top = paths.generated_root / "algo_top.v"
    if not top.is_file():
        return _CheckOutput(
            StatusSection("Generated artifacts", NOT_CONFIGURED, "not generated yet"),
            [Action(
                id="build-design",
                description="Generate the structural top level",
                command=_build_command(paths),
            )],
        )

    generated_at = top.stat().st_mtime
    inputs = [
        p for p in
        [config.root / ROOT_CONFIG_NAME, paths.design_yml, paths.modules_yml]
        + config.resolve_sources("rtl") + config.resolve_sources("hls")
        if p.is_file()
    ]
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
                command=_build_command(paths),
                auto_fixable=True,
            )],
        )
    return _CheckOutput(StatusSection(
        "Generated artifacts", PASS, paths.relative(top)))


def _build_command(paths: ProjectPaths) -> str:
    """The exact ``forge build`` invocation for this project.

    Spelled out in full — design, registry, consumer root and output — so it
    can be pasted from the terminal and run from the project root without
    the reader having to know which of ``build``'s defaults apply to a
    ``.forge/`` layout.
    """
    return (
        f"forge build {paths.relative(paths.design_yml)} "
        f"--contracts-from {paths.relative(paths.modules_yml)} "
        f"--consumer-root {paths.relative(paths.root) or '.'} "
        f"--output {paths.relative(paths.generated_root / 'algo_top.v')} --apply"
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
