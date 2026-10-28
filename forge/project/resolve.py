"""The one way a command finds the project it was run in.

``forge build``, ``forge test``, ``forge report`` and ``forge inspect``
called without a design path all come here: find ``forge.yml`` above the
working directory, regenerate ``.forge/`` from it and the sources, and hand
back the paths the command would otherwise have needed on its command line.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from forge.project.adopt import plan_derived_state
from forge.project.config import ForgeConfig
from forge.project.discovery import Ambiguity
from forge.project.paths import ROOT_CONFIG_NAME, ProjectPaths, find_project_root


@dataclass(frozen=True)
class ResolvedProject:
    """A ``forge.yml`` project, with ``.forge/`` written for it."""

    config: ForgeConfig
    paths: ProjectPaths

    @property
    def root(self) -> Path:
        return self.config.root

    @property
    def design(self) -> Path:
        return self.paths.design_yml

    @property
    def registry(self) -> Path:
        return self.paths.modules_yml

    def top_level(self, mode: str = "verilog", top_name: str = "algo_top") -> Path:
        """Where ``forge build`` writes the generated top level for *mode*."""
        if mode == "vhdl":
            return self.paths.generated_root / f"{top_name}.vhd"
        if mode == "bd":
            return self.paths.generated_root / "block_design.tcl"
        return self.paths.generated_root / f"{top_name}.v"

    @property
    def verification(self) -> Optional[Path]:
        """The user's ``design.verification.yml``, when ``forge.yml`` names one."""
        if not self.config.verification_config:
            return None
        return (self.config.root / self.config.verification_config).resolve()


def resolve_project(start: "Path | str" = ".") -> ResolvedProject:
    """Find the project around *start* and bring ``.forge/`` up to date.

    Raises ``FileNotFoundError`` outside a project and ``ValueError`` while
    ``forge.yml`` leaves a question open that generation would otherwise
    have to guess — both classified as a problem with the user's input.
    """
    root = find_project_root(start)
    if root is None:
        raise FileNotFoundError(
            f"no {ROOT_CONFIG_NAME} in {Path(start).resolve()} or any parent directory — "
            f"run `forge adopt` to create one, or pass the design file explicitly"
        )
    config = ForgeConfig.load(root / ROOT_CONFIG_NAME)
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
    return ResolvedProject(config=config, paths=paths)


def _unresolved_message(unresolved: List[Ambiguity]) -> str:
    lines = [f"{len(unresolved)} decision(s) are still open in {ROOT_CONFIG_NAME}:"]
    lines.extend(f"  - {item.question}" for item in unresolved)
    lines.append("Run `forge check` to see each one, or `forge adopt --interactive` to answer them.")
    return "\n".join(lines)
