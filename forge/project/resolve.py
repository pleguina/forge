"""The one way a command finds the project it was run in.

``forge build``, ``forge test``, ``forge report`` and ``forge inspect``
called without a design path all come here: find ``forge.yml`` above the
working directory and hand back the paths the command would otherwise have
needed on its command line. A project whose ``forge.yml`` names its own
``design:`` uses that design as it stands; any other project has
``.forge/`` regenerated from ``forge.yml`` and the sources first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from forge.project.adopt import plan_derived_state
from forge.project.config import BUILD_PATH_OPTIONS, ForgeConfig
from forge.project.discovery import Ambiguity
from forge.project.paths import ROOT_CONFIG_NAME, ProjectPaths, find_project_root


@dataclass(frozen=True)
class ResolvedProject:
    """A ``forge.yml`` project, ready for a command to run on."""

    config: ForgeConfig
    paths: ProjectPaths
    design: Path
    #: ``None`` when an authored design names no registry of its own.
    registry: Optional[Path]

    @property
    def root(self) -> Path:
        return self.config.root

    def top_level(self, mode: str = "verilog", top_name: str = "algo_top") -> Path:
        """Where ``forge build`` writes the generated top level for *mode*."""
        return self.paths.top_level(mode, top_name)

    @property
    def verification(self) -> Optional[Path]:
        """The user's ``design.verification.yml``, when ``forge.yml`` names one."""
        if not self.config.verification_config:
            return None
        return (self.config.root / self.config.verification_config).resolve()

    def build_options(self) -> Dict[str, str]:
        """``forge.yml``'s ``build:`` options, path-valued ones made absolute."""
        return {
            name: str((self.root / value).resolve()) if name in BUILD_PATH_OPTIONS else value
            for name, value in self.config.build.items()
        }


def resolve_project(start: "Path | str" = ".") -> ResolvedProject:
    """Find the project around *start* and make it ready to run on.

    Raises ``FileNotFoundError`` outside a project or when a file
    ``forge.yml`` names is missing, and ``ValueError`` while ``forge.yml``
    leaves a question open that generation would otherwise have to guess —
    both classified as a problem with the user's input.
    """
    root = find_project_root(start)
    if root is None:
        raise FileNotFoundError(
            f"no {ROOT_CONFIG_NAME} in {Path(start).resolve()} or any parent directory — "
            f"run `forge adopt` to create one, or pass the design file explicitly"
        )
    config = ForgeConfig.load(root / ROOT_CONFIG_NAME)
    if config.authored:
        return _authored(config)

    plan = plan_derived_state(config)
    if plan.unresolved:
        raise ValueError(_unresolved_message(plan.unresolved))
    if not plan.discovery.managed_units:
        raise ValueError(
            f"{ROOT_CONFIG_NAME}'s sources match no module — check `sources:` in "
            f"{root / ROOT_CONFIG_NAME}"
        )
    paths = config.paths()
    # A contract left over from a module that was removed, or that now has
    # one of its own in forge.yml, would otherwise outlive its source.
    if paths.contract_root.is_dir():
        for stale in paths.contract_root.glob("*.interface.yaml"):
            if stale not in plan.files:
                stale.unlink()
    plan.write()
    return ResolvedProject(
        config=config, paths=paths, design=paths.design_yml, registry=paths.modules_yml,
    )


def _authored(config: ForgeConfig) -> ResolvedProject:
    assert config.design is not None
    design = (config.root / config.design).resolve()
    if not design.is_file():
        raise FileNotFoundError(f"{ROOT_CONFIG_NAME} names design {config.design}, which does not exist")
    if config.registry:
        registry: Optional[Path] = (config.root / config.registry).resolve()
        if not registry.is_file():
            raise FileNotFoundError(
                f"{ROOT_CONFIG_NAME} names registry {config.registry}, which does not exist"
            )
    else:
        registry = design_registry(design)
    paths = config.paths()
    ignore = paths.state_root / ".gitignore"
    if not ignore.exists():
        from forge.project.adopt import STATE_GITIGNORE

        ignore.parent.mkdir(parents=True, exist_ok=True)
        ignore.write_text(STATE_GITIGNORE)
    return ResolvedProject(config=config, paths=paths, design=design, registry=registry)


def design_registry(design: Path) -> Optional[Path]:
    """The ``modules.yml`` a design's own ``registry:`` key names, if any."""
    import yaml

    try:
        document = yaml.safe_load(design.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    name = document.get("registry") if isinstance(document, dict) else None
    if not name:
        return None
    return (design.parent / str(name)).resolve()


def _unresolved_message(unresolved: List[Ambiguity]) -> str:
    lines = [f"{len(unresolved)} decision(s) are still open in {ROOT_CONFIG_NAME}:"]
    lines.extend(f"  - {item.question}" for item in unresolved)
    lines.append("Run `forge check` to see each one, or `forge adopt --interactive` to answer them.")
    return "\n".join(lines)
