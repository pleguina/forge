"""Named variants of a project's design: ``forge.yml``'s ``variants:``.

A board build, a simulation build and a debug build differ in a few places.
``forge.yml`` names each variant once, as a design file written with
``variant_of:`` or as the changes themselves, applied to the project's
design with the same keys (:mod:`forge.contracts.design_variant`):

    variants:
      board: designs/design.board.yml
      sim:
        remove: {modules: [link_monitor]}
        modules: {sector: {instances: 2}}

``forge build --variant sim`` and ``forge inspect --variant sim`` work on it,
and a verification target with ``variant: sim`` simulates it. Everything a
variant generates goes under ``.forge/variants/<name>/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forge.contracts.design_variant import apply_variant, load_design_unexpanded, relocate_design


def variant_dir(paths: Any, name: str) -> Path:
    """Where everything variant *name* generates goes."""
    return paths.state_root / "variants" / name


def variant_design(config: Any, base_design: Path, name: str) -> Path:
    """The design file of variant *name* of *config*'s project, whose own
    design is *base_design*; changes written in ``forge.yml`` are applied and
    the result written under the variant's directory."""
    if name not in config.variants:
        known = ", ".join(sorted(config.variants)) or "none"
        raise ValueError(f"no variant {name!r} under variants: in forge.yml (have {known})")
    spec = config.variants[name]
    if isinstance(spec, str):
        path = (config.root / spec).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"variants.{name} names {spec}, which does not exist")
        return path
    design = apply_variant(load_design_unexpanded(base_design), dict(spec),
                           f"{config.root / 'forge.yml'}: variants.{name}")
    out = variant_dir(config.paths(), name) / "design.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(relocate_design(design, base_design.parent), sort_keys=False))
    return out
