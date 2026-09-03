"""Bring a whole project up to the schemas this FORGE understands.

``forge topgen migrate`` has done the individual migrations for a while, and
does them well — but it asks the user to already know the answer: which
files need migrating, and which of the four migrations each one needs. That
is fine for the author of the migration and useless for the person who just
upgraded FORGE and wants to know whether their project still works.

This module answers the question they actually have. Point it at a project;
it finds every file, works out what each one needs, and hands back one
preview of the whole chain.

It reimplements nothing. Every migration is performed by the function in
:mod:`forge.generation.migrate` that already owns it, so a project migrated
here and one migrated file-by-file end up byte-identical — and a fix to a
migration rule fixes both at once.

Three properties the plan asks for and this enforces:

* **Dry-run by default.** Nothing is written without ``--apply``.
* **Idempotent.** Running it twice is running it once; a migration that
  would change nothing is not offered.
* **Honest about what it will not do.** A migration that *moves files*
  (the legacy plugin layout) is reported with the command that performs it
  rather than performed here, because a command that silently reorganises
  someone's repository is not one they can trust to preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from forge.project.actions import Action
from forge.project.config import FORGE_YML_SCHEMA_VERSION, ForgeConfig
from forge.project.paths import ROOT_CONFIG_NAME, ProjectPaths, find_project_root


@dataclass
class Migration:
    """One *file's* migration, computed but not applied.

    One per file rather than one per rule, because several rules can apply
    to the same file — a contract can both need its schema version declared
    and carry a legacy ``partition:`` label. Computing those independently
    from what is on disk and then writing each in turn means the last write
    silently discards the others, which is exactly the bug that produced a
    migration that was not idempotent. So the rules **compose**: each is
    handed the result of the previous one, and the file is written once.

    Held as whole before/after content rather than a patch, for the same
    reason ``forge fix`` does: applying is a single write, the diff is
    derived rather than assembled, and the preview cannot drift from what
    lands.
    """

    path: Path
    before: str
    after: str
    #: The rule ids applied, in order. ``--only`` filters on these.
    steps: Tuple[str, ...] = ()
    #: One line per step, for the report.
    titles: Tuple[str, ...] = ()

    @property
    def id(self) -> str:
        """The first step's id — enough to identify a single-step migration
        and honest about a composed one being more than that."""
        return self.steps[0] if self.steps else ""

    @property
    def title(self) -> str:
        return "; ".join(self.titles) or self.path.name

    @property
    def changes_anything(self) -> bool:
        return self.before != self.after

    def diff(self) -> List[str]:
        import difflib

        return list(difflib.unified_diff(
            self.before.splitlines(), self.after.splitlines(),
            fromfile=f"a/{self.path.name}", tofile=f"b/{self.path.name}",
            lineterm="", n=2,
        ))

    def apply(self) -> Path:
        self.path.write_text(self.after)
        return self.path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "steps": list(self.steps),
            "titles": list(self.titles),
            "path": str(self.path),
            "diff": self.diff(),
        }


@dataclass
class MigrationPlan:
    """Everything :func:`plan_migration` found."""

    root: Path
    #: The schemas this FORGE build writes, for the report header.
    target_versions: Dict[str, str] = field(default_factory=dict)
    migrations: List[Migration] = field(default_factory=list)
    #: Migrations this command will not perform itself, with the command
    #: that does — currently the ones that move files.
    manual: List[Action] = field(default_factory=list)
    #: Things worth saying before anything is written.
    notes: List[str] = field(default_factory=list)
    applied: List[Path] = field(default_factory=list)

    @property
    def pending(self) -> List[Migration]:
        return [m for m in self.migrations if m.changes_anything]

    @property
    def up_to_date(self) -> bool:
        return not self.pending and not self.manual

    def apply(self) -> List[Path]:
        for migration in self.pending:
            self.applied.append(migration.apply())
        return self.applied

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": str(self.root),
            "target_versions": dict(self.target_versions),
            "migrations": [m.to_dict() for m in self.pending],
            "manual": [a.to_dict() for a in self.manual],
            "notes": list(self.notes),
            "applied": [str(p) for p in self.applied],
            "up_to_date": self.up_to_date,
        }


# ── Planning ──────────────────────────────────────────────────────────────

def plan_migration(
    root: "Path | str" = ".", *, only: Optional[List[str]] = None,
) -> MigrationPlan:
    """Work out every migration this project needs, writing nothing.

    Args:
        root: Anywhere inside the project. Both layouts are recognised —
            the ``forge.yml`` + ``.forge/`` one and today's
            ``plugins/<id>/forge/`` one — because a project that has not
            migrated yet is precisely the one that needs this command.
        only: Restrict to these migration ids.
    """
    from forge.contracts.config import DESIGN_SCHEMA_VERSION, MODULE_REGISTRY_SCHEMA_VERSION
    from forge.contracts.contract_loader import INTERFACE_CONTRACT_SCHEMA_VERSION

    start = Path(root).expanduser().resolve()
    project_root = find_project_root(start) or start
    plan = MigrationPlan(
        root=project_root,
        target_versions={
            "forge.yml": FORGE_YML_SCHEMA_VERSION,
            "design": DESIGN_SCHEMA_VERSION,
            "registry": MODULE_REGISTRY_SCHEMA_VERSION,
            "interface": INTERFACE_CONTRACT_SCHEMA_VERSION,
        },
    )

    for path in [project_root / ROOT_CONFIG_NAME] + project_files(project_root):
        if not path.is_file():
            continue
        try:
            before = path.read_text()
        except OSError:
            continue

        working = before
        steps: List[str] = []
        titles: List[str] = []
        for rule_id, rule in RULES:
            if only and rule_id not in only:
                continue
            outcome = rule(path, working)
            if outcome is None:
                continue
            title, migrated = outcome
            if migrated == working:
                continue
            working = migrated
            steps.append(rule_id)
            titles.append(title)

        if working != before:
            plan.migrations.append(Migration(
                path=path, before=before, after=working,
                steps=tuple(steps), titles=tuple(titles),
            ))

    plan.manual = _manual_migrations(project_root)
    plan.notes = _notes(project_root, plan)
    return plan


def project_files(root: Path) -> List[Path]:
    """Every schema-bearing file in the project, in both layouts.

    Deliberately not driven by ``forge.yml``: a project that predates it
    has none, and that project is the reason this command exists. Sorted so
    a preview reads the same way twice.
    """
    candidates: List[Path] = []

    paths = ProjectPaths.for_root(root)
    for directory in (paths.project_config_root, paths.contract_root):
        if directory.is_dir():
            candidates.extend(sorted(directory.glob("*.yml")))
            candidates.extend(sorted(directory.glob("*.yaml")))

    # The legacy plugin layout: <root>/forge/ and plugins/<id>/forge/.
    legacy_roots = [root / "forge"]
    plugins_dir = root / "plugins"
    if plugins_dir.is_dir():
        legacy_roots.extend(sorted(p / "forge" for p in plugins_dir.iterdir() if p.is_dir()))
    for legacy in legacy_roots:
        if not legacy.is_dir():
            continue
        candidates.extend(sorted(legacy.glob("modules.yml")))
        candidates.extend(sorted((legacy / "designs").glob("*.yml")))
        candidates.extend(sorted((legacy / "interfaces").glob("*.interface.yaml")))
        candidates.extend(sorted((legacy / "verify").glob("design.verification.yml")))

    seen: List[Path] = []
    for path in candidates:
        if path.is_file() and path not in seen:
            seen.append(path)
    return seen


# ── Individual finders ────────────────────────────────────────────────────

def _rule_schema_version(path: Path, text: str) -> Optional[Tuple[str, str]]:
    """Declare the schema version in a file that omits one.

    An undeclared version is *silent* under FORGE's compatibility policy —
    fully backward compatible, never a warning. Migrating anyway is what
    lets the next major version's compatibility check say something useful
    about this project instead of shrugging.
    """
    from forge.generation.migrate import (
        _insert_interface_schema_version,
        _insert_top_level_key,
        _TOP_LEVEL_SCHEMA_KEY,
        detect_schema_kind,
    )
    from forge.contracts.contract_loader import INTERFACE_CONTRACT_SCHEMA_VERSION

    try:
        kind = detect_schema_kind(path)
    except ValueError:
        # A file whose schema this FORGE does not recognise is not this
        # command's to rewrite.
        return None

    try:
        if kind == "interface":
            migrated, changed = _insert_interface_schema_version(
                text, f'"{INTERFACE_CONTRACT_SCHEMA_VERSION}"')
        elif kind in _TOP_LEVEL_SCHEMA_KEY:
            key, value = _TOP_LEVEL_SCHEMA_KEY[kind]
            migrated, changed = _insert_top_level_key(text, key, value)
        else:
            return None
    except Exception:  # noqa: BLE001
        # A file too malformed to locate the insertion point in is
        # `forge check`'s finding, not this command's. Migration must not
        # become a second, worse validator that refuses to migrate the
        # rest of the project because one file is broken.
        return None

    if not changed:
        return None
    return f"declare the {kind} schema version", migrated


def _rule_forge_yml_version(path: Path, text: str) -> Optional[Tuple[str, str]]:
    """Bring ``forge.yml``'s own ``schema_version`` up to date.

    Currently a no-op for every project, since ``forge.yml`` has only ever
    had one version — which is exactly when to wire the path, so the first
    real bump has somewhere to land rather than needing this command
    invented under time pressure.
    """
    if path.name != ROOT_CONFIG_NAME:
        return None
    try:
        config = ForgeConfig.load(path)
    except Exception:  # noqa: BLE001
        return None
    if config.schema_version == FORGE_YML_SCHEMA_VERSION:
        return None
    previous = config.schema_version
    config.schema_version = FORGE_YML_SCHEMA_VERSION
    return f"{previous} -> {FORGE_YML_SCHEMA_VERSION}", config.to_yaml()


def _rule_partition_to_coordinates(path: Path, text: str) -> Optional[Tuple[str, str]]:
    """Wrap a bare ``partition:`` label as a structured ``coordinates:``.

    Offered rather than urged: ``partition:`` remains fully supported, and
    the wrap is single-axis — there is no safe general way to decompose an
    arbitrary label into several named axes without the user saying how. The
    axis is named ``partition`` for exactly that reason: it preserves the
    information without inventing a meaning for it.
    """
    from forge.generation.migrate import partition_to_coordinates

    if not path.name.endswith(".interface.yaml"):
        return None
    try:
        migrated, roles = partition_to_coordinates(text, axis="partition")
    except Exception:  # noqa: BLE001 — a broken contract is `forge check`'s finding
        return None
    if not roles:
        return None
    return (
        f"wrap partition as coordinates for {len(roles)} role(s) "
        f"({', '.join(roles)})",
        migrated,
    )


# ── What this command declines to do ──────────────────────────────────────

def _manual_migrations(root: Path) -> List[Action]:
    """Migrations that move files, reported rather than performed.

    A command someone runs to *preview* a change must not be one that could
    reorganise their repository, so these are surfaced with the command that
    performs them and left alone.
    """
    from forge.generation.migrate import (
        find_legacy_plugin_layout,
        find_legacy_verify_contract_name,
    )

    actions: List[Action] = []
    roots = [root]
    plugins_dir = root / "plugins"
    if plugins_dir.is_dir():
        roots.extend(sorted(p for p in plugins_dir.iterdir() if p.is_dir()))

    for plugin_root in roots:
        try:
            issue = find_legacy_plugin_layout(plugin_root)
        except Exception:  # noqa: BLE001
            continue
        if not issue.is_clean():
            actions.append(Action(
                id="migrate-legacy-plugin-layout",
                description=(
                    f"{plugin_root.name}: verify/ still sits outside forge/ (or its "
                    f"bootstrap still carries a _FW_PYTHON block) — this migration "
                    f"moves files, so it is not performed by a preview command"
                ),
                command=(
                    f"forge topgen migrate --kind legacy-plugin-layout "
                    f"--plugin-root {plugin_root}"
                ),
                safe=False,
            ))
        try:
            legacy_contract = find_legacy_verify_contract_name(plugin_root)
        except Exception:  # noqa: BLE001
            legacy_contract = None
        if legacy_contract is not None:
            actions.append(Action(
                id="migrate-verify-contract-name",
                description=(
                    f"{plugin_root.name}: the verification contract still uses its "
                    f"legacy filename ({legacy_contract.name}) — renaming is a file "
                    f"move, so it is not performed here"
                ),
                command=(
                    f"forge topgen migrate --kind rename-verify-contract "
                    f"--plugin-root {plugin_root}"
                ),
                safe=False,
            ))
    return actions


def _notes(root: Path, plan: MigrationPlan) -> List[str]:
    """What to say before rewriting someone's files."""
    notes: List[str] = []
    if plan.pending and not (root / ".git").is_dir():
        notes.append(
            "This project is not a git repository, so there is nothing to diff "
            "against or revert to. Take a copy before running --apply."
        )
    return notes


#: A rule takes a path and the file's *current* content — which may already
#: carry an earlier rule's change — and returns ``(title, new content)``, or
#: ``None`` when it does not apply. Content in, content out, so rules
#: compose instead of racing each other to the same file.
RuleFn = Callable[[Path, str], Optional[Tuple[str, str]]]

#: Version declarations first, so a later rule's change is made to a file
#: that already states which schema it is.
RULES: List[Tuple[str, RuleFn]] = [
    ("forge-yml-version", _rule_forge_yml_version),
    ("schema-version", _rule_schema_version),
    ("partition-to-coordinates", _rule_partition_to_coordinates),
]
