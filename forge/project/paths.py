"""Where a FORGE project's files live — resolved once, passed everywhere.

Subsystems across FORGE build paths by hand out of hardcoded fragments:
``plugins/<id>/forge``, ``plugins/<id>/algo``, ``gen-top/design_<id>``. That
is the single reason FORGE cannot be pointed at a repository it did not
create — the layout is not a setting, it is spelled into the code. The plan's
Phase D1 asks for exactly one resolved structure that every subsystem
receives instead.

:class:`ProjectPaths` is that structure, with two constructors for the two
layouts that must both keep working:

* :meth:`ProjectPaths.for_root` — the ``forge.yml`` + ``.forge/`` layout
  ``forge adopt`` writes, where user code stays where it already is and every
  generated/resolved artifact lives under one ignorable directory.
* :meth:`ProjectPaths.for_legacy_plugin` — today's ``plugins/<id>/forge/``
  layout, unchanged, so existing projects keep resolving identically.

Nothing here touches the filesystem except :meth:`ProjectPaths.ensure`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

#: Directory holding everything FORGE generates or resolves. One directory,
#: so a project can ignore it in version control with one line and delete it
#: to force a clean regeneration.
STATE_DIR_NAME = ".forge"

#: The user-authored project file at the repository root.
ROOT_CONFIG_NAME = "forge.yml"


@dataclass(frozen=True)
class ProjectPaths:
    """Every directory a FORGE subsystem is allowed to care about.

    Attributes:
        root: Project root — the directory holding ``forge.yml``, or the
            repository root for a legacy plugin layout.
        config: The user-authored project file this structure was resolved
            from.
        source_roots: Directories containing the user's own HDL/HLS sources.
            Never written to.
        state_root: Parent of every generated/resolved directory below.
        project_config_root: Resolved ``modules.yml``/``design.yml``.
        contract_root: Resolved/inferred interface contracts.
        generated_root: Generated HDL (the structural top level).
        ir_root: Canonical IR documents.
        report_root: Report bundles and dashboards.
        cache_root: Anything that may be deleted without losing information.
        provenance_root: Provenance records.
    """

    root: Path
    config: Path
    source_roots: Tuple[Path, ...]
    state_root: Path
    project_config_root: Path
    contract_root: Path
    generated_root: Path
    ir_root: Path
    report_root: Path
    cache_root: Path
    provenance_root: Path

    # ── Constructors ──────────────────────────────────────────────────────

    @classmethod
    def for_root(
        cls,
        root: "Path | str",
        *,
        source_roots: Iterable["Path | str"] = (),
        config: Optional["Path | str"] = None,
    ) -> "ProjectPaths":
        """Resolve the ``forge.yml`` + ``.forge/`` layout under *root*.

        This is the layout ``forge adopt`` writes into an existing
        repository: the user's tree is left exactly as it was, and
        everything FORGE produces is confined to ``<root>/.forge/``.
        """
        root_path = Path(root).expanduser().resolve()
        state = root_path / STATE_DIR_NAME
        return cls(
            root=root_path,
            config=(
                Path(config).expanduser().resolve()
                if config is not None
                else root_path / ROOT_CONFIG_NAME
            ),
            source_roots=tuple(
                (root_path / Path(s)).resolve() if not Path(s).is_absolute()
                else Path(s).resolve()
                for s in source_roots
            ),
            state_root=state,
            project_config_root=state / "project",
            contract_root=state / "contracts",
            generated_root=state / "generated",
            ir_root=state / "ir",
            report_root=state / "reports",
            cache_root=state / "cache",
            provenance_root=state / "provenance",
        )

    @classmethod
    def for_legacy_plugin(
        cls,
        plugins_root: "Path | str",
        plugin_id: str,
        *,
        consumer_root: Optional["Path | str"] = None,
        algo_root: Optional["Path | str"] = None,
    ) -> "ProjectPaths":
        """Resolve today's ``plugins/<id>/forge/`` layout.

        The mapping is taken from what ``forge init`` actually creates, so a
        subsystem migrated onto :class:`ProjectPaths` keeps resolving the
        very same files for an existing plugin. Nothing is moved and nothing
        is deprecated here — this constructor exists so the *callers* can
        stop hardcoding the layout, which is the prerequisite for supporting
        any other one.
        """
        plugins = Path(plugins_root).expanduser().resolve()
        algo = Path(algo_root).expanduser().resolve() if algo_root else plugins
        consumer = (
            Path(consumer_root).expanduser().resolve()
            if consumer_root
            else plugins.parent
        )
        forge_root = plugins / plugin_id / "forge"
        return cls(
            root=consumer,
            config=forge_root / "designs" / "design.yml",
            source_roots=(algo / plugin_id / "algo",),
            state_root=forge_root,
            project_config_root=forge_root,
            contract_root=forge_root / "interfaces",
            generated_root=consumer / "gen-top" / f"design_{plugin_id}",
            ir_root=consumer / "gen-top" / f"design_{plugin_id}",
            report_root=plugins / plugin_id / "report",
            cache_root=consumer / "build",
            provenance_root=plugins / plugin_id / "report",
        )

    # ── Well-known files ──────────────────────────────────────────────────

    @property
    def modules_yml(self) -> Path:
        """The module registry."""
        return self.project_config_root / "modules.yml"

    @property
    def design_yml(self) -> Path:
        """The design topology.

        Under the legacy layout ``design.yml`` sits in a ``designs/``
        subdirectory, which is why this reads ``config`` there rather than
        assuming one spelling.
        """
        if self.config.name == "design.yml":
            return self.config
        return self.project_config_root / "design.yml"

    @property
    def ir_json(self) -> Path:
        return self.ir_root / "design.ir.json"

    @property
    def verification_yml(self) -> Path:
        return self.project_config_root / "design.verification.yml"

    # ── Operations ────────────────────────────────────────────────────────

    def ensure(self, *paths: Path) -> None:
        """Create the given state directories (default: all of them).

        Only ever creates directories under :attr:`state_root` plus the
        report root — never a source root, which belongs to the user.
        """
        targets = paths or (
            self.state_root,
            self.project_config_root,
            self.contract_root,
            self.generated_root,
            self.ir_root,
            self.report_root,
            self.cache_root,
            self.provenance_root,
        )
        for path in targets:
            path.mkdir(parents=True, exist_ok=True)

    def relative(self, path: "Path | str") -> str:
        """*path* relative to :attr:`root` when it is inside it.

        Used for display and for writing configuration that must stay
        valid when the repository is cloned elsewhere. Falls back to the
        absolute path when the target genuinely lives outside the project.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = (self.root / candidate).resolve()
        try:
            return str(candidate.relative_to(self.root))
        except ValueError:
            return str(candidate)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": str(self.root),
            "config": str(self.config),
            "source_roots": [str(p) for p in self.source_roots],
            "state_root": str(self.state_root),
            "project_config_root": str(self.project_config_root),
            "contract_root": str(self.contract_root),
            "generated_root": str(self.generated_root),
            "ir_root": str(self.ir_root),
            "report_root": str(self.report_root),
            "cache_root": str(self.cache_root),
            "provenance_root": str(self.provenance_root),
        }


def find_project_root(start: "Path | str") -> Optional[Path]:
    """Walk up from *start* looking for a ``forge.yml``.

    Returns the directory containing it, or ``None`` — so ``forge check``
    run from a subdirectory finds the project the way ``git`` does, and can
    tell the difference between "not a FORGE project" and "a broken one".
    """
    current = Path(start).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        if (directory / ROOT_CONFIG_NAME).is_file():
            return directory
    return None
