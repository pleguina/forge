"""A design written as the changes it makes to another design.

A simulation build often differs from the board build in a handful of
places: a delay that only fixes routing is left out, a register bus the
board provides becomes a plain input. Copying the whole design for that lets
the two drift apart; a variant names its base and lists only the
differences:

    variant_of: design.rpc_fp.yml
    remove:
      modules: [gmt_tmux_dly]              # with every connection and topology group touching it
      connections: [{from: a, to: b}]
      topology_groups: [cfg_to_dt]
    modules:
      dt: {external_in_ports: [csp_in, cfg64, cfg_valid]}   # null removes a field
    add:                                   # appended to the base's lists, merged into its mappings
      allowed_unconnected: {open_outputs: [dt.mon]}
    clock_period: 4.0                      # any other top-level key replaces the base's

The base must sit in the same directory, so the relative paths it holds
(``registry:``, ``src:``) mean the same thing in the variant. Every name the
variant uses must exist in its base: a module renamed there is an error
here, not a change the variant silently stops making.

:func:`load_design_document` is the one way FORGE reads a design file, so
generation, validation and analysis all see the resolved design.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml

VARIANT_KEY = "variant_of"
_VARIANT_KEYS = (VARIANT_KEY, "remove", "modules", "add")
_REMOVABLE = ("modules", "connections", "topology_groups")


def load_design_document(path: "Path | str") -> Dict[str, Any]:
    """The design at *path* as a plain mapping, with ``variant_of:`` applied."""
    return _load(Path(path).expanduser().resolve(), [])


def design_sources(path: "Path | str") -> List[Path]:
    """*path* and every base it is a variant of, nearest first.

    For freshness checks only: a file it cannot follow ends the list, and
    loading the design is what reports the problem."""
    sources = [Path(path).expanduser().resolve()]
    while True:
        try:
            base = _read(sources[-1]).get(VARIANT_KEY)
            if not base:
                return sources
            nxt = _base_path(sources[-1], base)
        except (OSError, ValueError, yaml.YAMLError):
            return sources
        if nxt in sources:
            return sources
        sources.append(nxt)


def _read(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"design file not found: {path}")
    document = yaml.safe_load(path.read_text()) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{path}: a design must be a mapping")
    return document


def _base_path(path: Path, base: Any) -> Path:
    resolved = (path.parent / str(base)).resolve()
    if resolved.parent != path.parent:
        raise ValueError(
            f"{path}: variant_of must name a design in the same directory, got {base!r}"
        )
    return resolved


def _load(path: Path, seen: List[Path]) -> Dict[str, Any]:
    if path in seen:
        raise ValueError(f"{seen[0]}: variant_of loops back to {path.name}")
    document = _read(path)
    base_name = document.get(VARIANT_KEY)
    if not base_name:
        return document
    base = _load(_base_path(path, base_name), seen + [path])
    return _apply(base, document, path)


def _apply(base: Dict[str, Any], variant: Dict[str, Any], path: Path) -> Dict[str, Any]:
    design = copy.deepcopy(base)
    design.pop(VARIANT_KEY, None)

    remove = variant.get("remove") or {}
    unknown = sorted(set(remove) - set(_REMOVABLE))
    if unknown:
        raise ValueError(f"{path}: remove: takes {', '.join(_REMOVABLE)}, not {', '.join(unknown)}")

    modules = list(design.get("modules") or [])
    module_names = {m.get("name") for m in modules}
    gone: Set[str] = set(_names(remove.get("modules"), module_names, "module", path))
    design["modules"] = [m for m in modules if m.get("name") not in gone]

    connections = _remove_connections(
        list(design.get("connections") or []), remove.get("connections") or [], gone, path,
    )
    if "connections" in design:
        design["connections"] = connections

    groups = list(design.get("topology_groups") or [])
    dropped = set(_names(
        remove.get("topology_groups"), {g.get("name") for g in groups}, "topology group", path,
    ))
    if "topology_groups" in design:
        design["topology_groups"] = [
            g for g in groups
            if g.get("name") not in dropped and g.get("from") not in gone and g.get("to") not in gone
        ]

    by_name = {m.get("name"): m for m in design["modules"]}
    for name, fields in (variant.get("modules") or {}).items():
        if name not in by_name:
            raise ValueError(f"{path}: modules: {name!r} is not a module of the base design")
        for field, value in (fields or {}).items():
            if value is None:
                by_name[name].pop(field, None)
            else:
                by_name[name][field] = value

    _add(design, variant.get("add") or {}, path, "")

    for key, value in variant.items():
        if key not in _VARIANT_KEYS:
            design[key] = value
    return design


def _add(target: Dict[str, Any], extra: Dict[str, Any], path: Path, where: str) -> None:
    for key, value in extra.items():
        if key not in target or target[key] is None:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], list) and isinstance(value, list):
            target[key] = target[key] + copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _add(target[key], value, path, f"{where}{key}.")
        else:
            raise ValueError(
                f"{path}: add: {where}{key} is not a list or mapping in the base design; "
                "set it at the top level instead"
            )


def _names(requested: Any, known: Set[Any], kind: str, path: Path) -> List[str]:
    names = [str(n) for n in (requested or [])]
    missing = [n for n in names if n not in known]
    if missing:
        raise ValueError(f"{path}: remove: no {kind} named {', '.join(missing)} in the base design")
    return names


def _remove_connections(
    connections: List[Dict[str, Any]], requested: List[Any], gone: Set[str], path: Path,
) -> List[Dict[str, Any]]:
    """Drop the requested from/to pairs and everything touching a removed
    module; a fan-out connection loses only the destinations named."""
    pairs = set()
    for item in requested:
        if not isinstance(item, dict) or "from" not in item or "to" not in item:
            raise ValueError(f"{path}: remove: connections takes {{from: ..., to: ...}} entries, got {item!r}")
        pairs.add((str(item["from"]), str(item["to"])))

    kept: List[Dict[str, Any]] = []
    matched = set()
    for conn in connections:
        src = str(conn.get("from"))
        if src in gone:
            continue
        targets = conn.get("to")
        listed = targets if isinstance(targets, list) else [targets]
        remaining = []
        for dst in listed:
            if (src, str(dst)) in pairs:
                matched.add((src, str(dst)))
            elif str(dst) not in gone:
                remaining.append(dst)
        if not remaining:
            continue
        kept.append({**conn, "to": remaining if isinstance(targets, list) else remaining[0]})

    missing = sorted(pairs - matched)
    if missing:
        names = ", ".join(f"{s} -> {d}" for s, d in missing)
        raise ValueError(f"{path}: remove: no connection {names} in the base design")
    return kept
