"""Project-level discovery, adoption and health — the new-user surface.

The commands a newcomer meets (``forge adopt``, ``forge check``,
``forge next``) all rest on the same four ideas, and this package holds
exactly those:

* :mod:`~forge.project.paths` — where a project's files are, resolved once
  and passed around, so no subsystem hardcodes a layout again.
* :mod:`~forge.project.discovery` — what is in a repository FORGE has never
  seen, classified as known / inferred / ambiguous / unsupported.
* :mod:`~forge.project.adopt` — the project files that follow from a
  discovery, written without ever promoting a guess to fact.
* :mod:`~forge.project.status` — one model of project health, rendered as a
  report by ``forge check`` and as a single next step by ``forge next``.
* :mod:`~forge.project.explain` — a deterministic account of any one
  decision, drawn from the canonical IR where the design resolves and from
  discovery where it does not.
* :mod:`~forge.project.fix` — the repairs the project's own sources prove,
  and only those.
* :mod:`~forge.project.migrate` — bringing a whole project up to the
  schemas this build understands, in either layout.
* :mod:`~forge.project.hls_maturity` — how far an HLS module's interface
  can be trusted, and where a prediction and the built IP disagree.

:mod:`~forge.project.evidence` and :mod:`~forge.project.actions` are the two
small vocabularies the rest of them speak in: why FORGE concluded something,
and what the user should do about it.

This package sits alongside ``forge/contracts`` and ``forge/generation``
rather than inside ``forge/core``: it consumes them (contract skeletons come
from ``forge.generation.migrate``, so an adopted contract and a hand-authored
one are the same artifact), and ``forge/core`` deliberately depends on
nothing outside itself.
"""

from forge.project.actions import Action
from forge.project.config import ForgeConfig, ModulePolicy
from forge.project.evidence import Evidence
from forge.project.explain import Explanation, explain
from forge.project.fix import Fix, plan_fixes
from forge.project.hls_maturity import ModuleMaturity, Reconciliation, reconcile
from forge.project.migrate import Migration, plan_migration
from forge.project.paths import ProjectPaths, find_project_root

__all__ = [
    "Action", "Evidence", "Explanation", "Fix", "ForgeConfig", "Migration",
    "ModuleMaturity", "ModulePolicy", "ProjectPaths", "Reconciliation",
    "explain", "find_project_root", "plan_fixes", "plan_migration", "reconcile",
]
