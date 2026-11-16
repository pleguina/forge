"""A group of blocks and their wiring, defined once and used many times.

A payload repeats the same processing chain per sector, per link group, per
detector region. Writing its blocks and connections once keeps the copies
from drifting apart and makes the count a single number:

    subsystems:
      sector:
        modules:
          - {name: unpack, ref: unpacker, external_in_ports: [link]}
          - {name: sort, ref: sorter, parameters: {N: 4}}
        connections:
          - {from: unpack, to: sort, port_map: [[hits, hits]]}

    modules:
      - name: sector
        subsystem: sector
        instances: 6
        overrides:                     # exceptions, by copy
          3: {sort: {ref: sorter_v2}}  # fields replace the definition's
          5: {sort: null}              # this copy has no sort block
      - {name: merger, ref: merger}

    connections:
      - {from: sector.sort, to: merger, port_map: [[best, "best_{i}"]]}

Each copy's blocks become ordinary blocks named ``<copy>__<block>``, where
the copy is ``sector_<k>`` (``sector`` when there is one), so every instance
keeps its name when the count grows. A connection naming ``sector.<block>``
is made once per copy, with ``{i}`` replaced by the copy's index anywhere in
it; ``sector[3].<block>`` names one copy. Expansion runs after a variant is
applied, so a variant changes the count or adds an exception with the same
keys.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SUBSYSTEMS_KEY = "subsystems"
SEPARATOR = "__"
_ENTRY_KEYS = {"name", "subsystem", "instances", "overrides"}
_ENDPOINT = re.compile(r"^(?P<group>[A-Za-z_]\w*)(?:\[(?P<index>\d+)\])?\.(?P<block>[A-Za-z_]\w*)$")


def _listed(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else [value]


def _substitute(value: Any, index: int) -> Any:
    if isinstance(value, str):
        return value.replace("{i}", str(index))
    if isinstance(value, list):
        return [_substitute(v, index) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, index) for k, v in value.items()}
    return value


def expand_subsystems(design: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """*design* with every subsystem use replaced by the blocks and
    connections of its copies (see the module docstring)."""
    definitions = design.get(SUBSYSTEMS_KEY)
    if not definitions:
        return design
    if not isinstance(definitions, dict):
        raise ValueError(f"{path}: subsystems: want a mapping of name to its modules and connections")
    design = copy.deepcopy(design)
    design.pop(SUBSYSTEMS_KEY)
    for name, body in definitions.items():
        if not isinstance(body, dict) or not isinstance(body.get("modules"), list) or not body["modules"]:
            raise ValueError(f"{path}: subsystems.{name}: want modules: [...] and optionally connections: [...]")

    modules: List[Dict[str, Any]] = []
    connections: List[Dict[str, Any]] = []
    copies: Dict[str, List[Tuple[int, Dict[str, Optional[str]]]]] = {}
    for entry in design.get("modules") or []:
        group = entry.get("subsystem")
        if not group:
            modules.append(entry)
            continue
        where = f"{path}: modules: {entry.get('name')}"
        if group not in definitions:
            raise ValueError(f"{where}: no subsystem {group!r} (have {', '.join(map(str, definitions))})")
        extra = sorted(set(entry) - _ENTRY_KEYS)
        if extra:
            raise ValueError(f"{where}: a subsystem use takes {', '.join(sorted(_ENTRY_KEYS))}, "
                             f"not {', '.join(extra)}")
        count = int(entry.get("instances", 1))
        body = definitions[group]
        block_names = [str(b["name"]) for b in body["modules"]]
        overrides = {int(k): v or {} for k, v in (entry.get("overrides") or {}).items()}
        for k, over in overrides.items():
            if not 0 <= k < count:
                raise ValueError(f"{where}: overrides: copy {k} does not exist ({count} instances)")
            unknown = sorted(set(over) - set(block_names))
            if unknown:
                raise ValueError(f"{where}: overrides: {k}: no block {', '.join(unknown)} in subsystem {group}")
        uses = copies.setdefault(str(entry["name"]), [])
        for k in range(count):
            copy_name = str(entry["name"]) if count == 1 else f"{entry['name']}_{k}"
            blocks: Dict[str, Optional[str]] = {}
            for block in body["modules"]:
                over = overrides.get(k, {})
                if block["name"] in over and over[block["name"]] is None:
                    blocks[block["name"]] = None
                    continue
                made = {**copy.deepcopy(block), **copy.deepcopy(over.get(block["name"]) or {})}
                made["name"] = f"{copy_name}{SEPARATOR}{block['name']}"
                modules.append(_substitute(made, k))
                blocks[block["name"]] = made["name"]
            uses.append((k, blocks))
            for conn in body.get("connections") or []:
                for end in [conn["from"], *_listed(conn["to"])]:
                    if end not in blocks:
                        raise ValueError(f"{path}: subsystems.{group}: connection endpoint {end!r} "
                                         "is not one of its modules")
                source = blocks[conn["from"]]
                targets = [blocks[t] for t in _listed(conn["to"]) if blocks[t] is not None]
                if source is None or not targets:
                    continue
                made = _substitute({**conn, "from": source}, k)
                made["to"] = targets if isinstance(conn["to"], list) else targets[0]
                connections.append(made)

    for conn in design.get("connections") or []:
        connections.extend(_expand_connection(conn, copies, path))
    design["modules"] = modules
    if connections or "connections" in design:
        design["connections"] = connections
    return design


def _resolve(endpoint: Any, copies: Dict[str, List[Tuple[int, Dict[str, Optional[str]]]]],
             path: Path) -> Optional[Dict[int, Optional[str]]]:
    """copy index -> block name for an endpoint into a subsystem, or None for
    an ordinary block."""
    m = _ENDPOINT.match(str(endpoint))
    if not m or m.group("group") not in copies:
        return None
    uses = copies[m.group("group")]
    if m.group("block") not in uses[0][1]:
        raise ValueError(f"{path}: connection endpoint {endpoint!r}: {m.group('group')} has no block "
                         f"{m.group('block')!r}")
    out = {k: blocks[m.group("block")] for k, blocks in uses}
    if m.group("index") is not None:
        k = int(m.group("index"))
        if k not in out:
            raise ValueError(f"{path}: connection endpoint {endpoint!r}: {m.group('group')} has {len(uses)} copies")
        return {k: out[k]}
    return out


def _expand_connection(conn: Dict[str, Any], copies: Dict[str, List[Tuple[int, Dict[str, Optional[str]]]]],
                       path: Path) -> List[Dict[str, Any]]:
    source = _resolve(conn.get("from"), copies, path)
    targets = [(t, _resolve(t, copies, path)) for t in _listed(conn.get("to"))]
    scoped = [r for r in [source, *(r for _, r in targets)] if r is not None and len(r) > 1]
    indices: List[Optional[int]]
    if not scoped:
        indices = [None]
    else:
        indices = list(sorted(scoped[0]))
        if any(sorted(r) != indices for r in scoped[1:]):
            raise ValueError(f"{path}: connection {conn.get('from')} -> {conn.get('to')}: "
                             "its subsystem endpoints have different numbers of copies")
    out: List[Dict[str, Any]] = []
    for k in indices:
        def pick(original: Any, resolved: Optional[Dict[int, Optional[str]]]) -> Optional[str]:
            if resolved is None:
                return str(original)
            return resolved[k] if k is not None and k in resolved else next(iter(resolved.values()))

        src = pick(conn.get("from"), source)
        dsts = [d for d in (pick(t, r) for t, r in targets) if d is not None]
        if src is None or not dsts:
            continue
        made = _substitute(dict(conn), k) if k is not None else dict(conn)
        made["from"] = src
        made["to"] = dsts if isinstance(conn.get("to"), list) else dsts[0]
        out.append(made)
    return out
