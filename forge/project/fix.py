"""Repair the problems that have exactly one correct answer.

``forge check`` finds a project's problems; most of them need a human,
because they are decisions. Some of them do not: a stale generated artifact,
a contract whose declared width contradicts the RTL that proves it, a
generated file that adoption would recreate identically. Making a user open
an editor for those is friction with no upside.

The line this module must not cross is the plan's, and it is drawn at
*evidence*, not at convenience:

* **Safe** — the fix restates something a source already proves. A contract
  width corrected from ``31`` to ``32`` because ``classifier.sv`` declares
  ``[31:0]`` is safe: FORGE is not choosing, it is transcribing.
* **Unsafe** — the fix invents meaning. Assigning ``family:
  reconstructed_muon``, or picking one of two equally valid consumers, is a
  decision about intent, and no amount of confidence makes it FORGE's to
  make.

Only safe fixes are ever applied. Unsafe ones are not offered with a
confirmation prompt either — they are simply not this command's business,
and are reported as work for the user with the command that helps.

Every fix is computed as a before/after pair before anything is written, so
``--dry-run`` and the real run are the same computation and the preview
cannot drift from what lands.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from forge.project.actions import Action
from forge.project.evidence import DETERMINISTIC, Evidence, evidence_list, is_safe_to_write
from forge.project.status import ProjectStatus, evaluate


@dataclass
class Fix:
    """One repair, computed but not yet applied.

    Attributes:
        id: Stable identifier for the *kind* of repair, so tests and
            ``--only`` select on it rather than on prose.
        title: One line naming what is wrong.
        path: The file the repair rewrites, or ``None`` for a repair that
            runs a command instead of editing (a regeneration).
        before / after: The file's whole content either side of the repair.
            Whole content rather than a patch so applying is a single write
            and the diff is derived, never assembled by hand.
        evidence: What proves the repair correct. A fix whose evidence is
            not wholly :data:`~forge.project.evidence.DETERMINISTIC` is not
            safe and is never applied.
        command: For a regeneration, the command that performs it.
    """

    id: str
    title: str
    path: Optional[Path] = None
    before: str = ""
    after: str = ""
    evidence: List[Evidence] = field(default_factory=list)
    command: Optional[str] = None

    @property
    def safe(self) -> bool:
        """Whether the sources prove this repair, rather than suggest it."""
        return is_safe_to_write(self.evidence)

    @property
    def changes_anything(self) -> bool:
        return self.command is not None or self.before != self.after

    def diff(self) -> List[str]:
        """The unified diff a user reviews before approving."""
        if self.command is not None:
            return [f"$ {self.command}"]
        name = self.path.name if self.path else "(unknown)"
        return list(difflib.unified_diff(
            self.before.splitlines(), self.after.splitlines(),
            fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="", n=2,
        ))

    def apply(self) -> Optional[Path]:
        """Write the repair. Returns the path changed, or ``None``.

        Refuses anything not proven safe, as a second line of defence
        behind the caller's own filtering — an unsafe fix reaching this
        method at all is a bug, and silently applying it would be the one
        failure mode this whole module exists to prevent.
        """
        if not self.safe:
            raise ValueError(
                f"refusing to apply {self.id!r}: it is not proven by the project's "
                f"own sources"
            )
        if self.path is None or self.command is not None:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.after)
        return self.path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "path": str(self.path) if self.path else None,
            "command": self.command,
            "safe": self.safe,
            "diff": self.diff(),
            "evidence": evidence_list(self.evidence),
        }


@dataclass
class FixPlan:
    """Everything :func:`plan_fixes` found, applied or not."""

    root: Path
    status: ProjectStatus
    fixes: List[Fix] = field(default_factory=list)
    #: Problems that are real but are the user's to decide.
    manual: List[Action] = field(default_factory=list)
    applied: List[Path] = field(default_factory=list)

    @property
    def safe_fixes(self) -> List[Fix]:
        return [f for f in self.fixes if f.safe and f.changes_anything]

    def apply(self) -> List[Path]:
        """Apply every safe fix, in order. Returns the paths changed."""
        for fix in self.safe_fixes:
            changed = fix.apply()
            if changed is not None:
                self.applied.append(changed)
        return self.applied

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": str(self.root),
            "fixes": [f.to_dict() for f in self.safe_fixes],
            "manual": [a.to_dict() for a in self.manual],
            "applied": [str(p) for p in self.applied],
        }


# ── Planning ──────────────────────────────────────────────────────────────

def plan_fixes(
    root: "Path | str" = ".",
    *,
    status: Optional[ProjectStatus] = None,
    only: Optional[List[str]] = None,
) -> FixPlan:
    """Work out every repair available, without writing anything.

    Args:
        root: Anywhere inside the project.
        status: An assessment already computed, to avoid re-scanning.
        only: Restrict to these fix ids.
    """
    resolved = status or evaluate(root)
    plan = FixPlan(root=Path(resolved.root), status=resolved)

    if resolved.discovery is None:
        # No project to repair. `forge check` already says so and recommends
        # `forge adopt`; repeating its diagnosis here would be a second,
        # worse copy of it.
        plan.manual = list(resolved.actions)
        return plan

    for finder in FINDERS:
        for fix in finder(resolved):
            if only and fix.id not in only:
                continue
            if fix.changes_anything:
                plan.fixes.append(fix)

    fixable = {f.id for f in plan.safe_fixes}
    plan.manual = [
        action for action in resolved.actions
        if not (action.auto_fixable and _fix_id_for(action.id) in fixable)
    ]
    return plan


def _fix_id_for(action_id: str) -> str:
    """Which fix, if any, discharges an :class:`Action`.

    Kept as an explicit map rather than a naming convention so an action and
    its fix can be renamed independently without one silently stopping to
    cover the other.
    """
    return {
        "rebuild-design": "regenerate-stale-artifacts",
        "generate-missing-contracts": "generate-missing-contract",
        "resolve-contract-drift": "correct-contract-drift",
    }.get(action_id, action_id)


# ── Individual finders ────────────────────────────────────────────────────

def _find_contract_drift(status: ProjectStatus) -> List[Fix]:
    """Correct a contract width the module's own source contradicts.

    The plan's worked example of a safe fix. The RTL declares ``[31:0]``, the
    contract says ``31``; there is exactly one right answer and the source
    holds it. Only ``width`` is corrected: a ``raw_port`` naming a port that
    does not exist could mean the port was renamed *or* the role was
    mis-bound, and choosing between those is a decision.
    """
    import yaml

    if status.paths is None or status.discovery is None:
        return []

    fixes: List[Fix] = []
    for unit in status.discovery.managed_units:
        contract = status.paths.contract_root / f"{unit.name}.interface.yaml"
        if not contract.is_file():
            continue
        text = contract.read_text()
        try:
            document = yaml.safe_load(text) or {}
        except Exception:  # noqa: BLE001
            continue
        roles = ((document.get("ip_interface") or {}).get("roles") or {})
        if not isinstance(roles, dict):
            continue

        after = text
        corrections: List[Evidence] = []
        for role_name, body in roles.items():
            fields = body if isinstance(body, dict) else {}
            declared = fields.get("width")
            if declared is None:
                continue
            port = unit.ports.get(str(fields.get("raw_port") or role_name))
            if port is None or int(declared) == port.width:
                continue
            after = _replace_role_width(after, role_name, int(declared), port.width)
            corrections.append(Evidence(
                source=str(unit.path),
                rule="width_declared_by_source",
                value=f"{role_name}: {declared} -> {port.width}",
                confidence=DETERMINISTIC,
            ))

        if corrections:
            fixes.append(Fix(
                id="correct-contract-drift",
                title=(
                    f"{contract.name}: {len(corrections)} width(s) contradict "
                    f"{unit.path.name}"
                ),
                path=contract,
                before=text,
                after=after,
                evidence=corrections,
            ))
    return fixes


def _replace_role_width(text: str, role: str, old: int, new: int) -> str:
    """Rewrite one role's ``width:`` line, leaving the rest of the file alone.

    A line edit rather than a YAML round-trip: re-dumping the document would
    discard every comment in a file whose comments are most of its value to
    the person maintaining it.
    """
    lines = text.splitlines(keepends=True)
    inside = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.rstrip(":") == role and stripped.endswith(":"):
            inside = True
            continue
        if inside:
            if stripped and not line[:1].isspace():
                break
            if stripped.startswith("width:"):
                value = stripped.split(":", 1)[1].strip()
                if value == str(old):
                    indent = line[: len(line) - len(line.lstrip())]
                    lines[index] = f"{indent}width: {new}\n"
                    break
            if stripped.endswith(":") and not stripped.startswith("#") and len(
                    line) - len(line.lstrip()) <= 4:
                break
    return "".join(lines)


def _find_missing_contracts(status: ProjectStatus) -> List[Fix]:
    """Generate a contract for a managed module that has none.

    Safe because the contract is generated *from the module's own ports* by
    the same generator ``forge contract infer`` uses, and is emitted as
    ``normalization_status: draft`` — so nothing it contains is presented as
    reviewed, and the semantics FORGE cannot infer stay absent rather than
    invented.
    """
    from forge.project.adopt import _render_contract

    if status.paths is None or status.discovery is None:
        return []

    fixes: List[Fix] = []
    for unit in status.discovery.managed_units:
        contract = status.paths.contract_root / f"{unit.name}.interface.yaml"
        if contract.is_file():
            continue
        fixes.append(Fix(
            id="generate-missing-contract",
            title=f"{unit.name}: no interface contract — generate one from its ports",
            path=contract,
            before="",
            after=_render_contract(unit),
            evidence=[Evidence(
                source=str(unit.path),
                rule="ports_read_from_source",
                value=f"{len(unit.ports)} ports",
                confidence=DETERMINISTIC,
            )],
        ))
    return fixes


def _find_stale_generated(status: ProjectStatus) -> List[Fix]:
    """Regenerate a top level whose inputs have changed since it was built.

    Expressed as a command rather than a file rewrite: regeneration is
    ``forge build``'s job, and reimplementing it here would create a second
    generator that could disagree with the real one.
    """
    section = status.section("Generated artifacts")
    if section is None or section.state != "STALE" or status.paths is None:
        return []

    from forge.project.status import _build_command

    return [Fix(
        id="regenerate-stale-artifacts",
        title="generated top level is older than its inputs — regenerate it",
        command=_build_command(status.paths),
        evidence=[Evidence(
            source=str(status.paths.generated_root / "algo_top.v"),
            rule="inputs_newer_than_output",
            value=section.detail,
            confidence=DETERMINISTIC,
        )],
    )]


def _find_missing_state_gitignore(status: ProjectStatus) -> List[Fix]:
    """Restore ``.forge/.gitignore``.

    Small, but its absence quietly commits every generated file in the
    project to version control — which is how a "why is my diff 4000 lines"
    afternoon starts.
    """
    from forge.project.adopt import _STATE_GITIGNORE

    if status.paths is None or not status.paths.state_root.is_dir():
        return []
    target = status.paths.state_root / ".gitignore"
    if target.is_file():
        return []
    return [Fix(
        id="restore-state-gitignore",
        title=".forge/.gitignore is missing — generated files would be committed",
        path=target,
        before="",
        after=_STATE_GITIGNORE,
        evidence=[Evidence(
            source=str(status.paths.state_root),
            rule="generated_state_directory",
            value="every file under .forge/ is regenerable",
            confidence=DETERMINISTIC,
        )],
    )]


FinderFn = Callable[[ProjectStatus], List[Fix]]

#: Ordered so a generated contract exists before anything that reads one,
#: and regeneration comes last — after every input it would consume has been
#: repaired.
FINDERS: List[FinderFn] = [
    _find_missing_contracts,
    _find_contract_drift,
    _find_missing_state_gitignore,
    _find_stale_generated,
]
