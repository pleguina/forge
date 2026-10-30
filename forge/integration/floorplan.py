"""SLR assignment of a block-design top level, from the framework's links and a declared floorplan.

One ``USER_SLR_ASSIGNMENT`` per block-design cell, no coordinates: the placer is
free inside the SLR. Cells get their SLR from, in this order:

- the floorplan's ``slr`` map: SLR -> cell name patterns (``fnmatch``);
- the links: the instance that receives a link's top-level port goes to the SLR
  of the link's endpoint (the resolved I/O), and so does an instance it feeds
  that no other link's data reaches (a link demux and the decoders behind it);
- their neighbours: a cell with none of the above takes the SLR most of its
  neighbours have, a few hops out (ties to the lowest SLR). An SLR listed in
  ``reserved`` is never taken this way.

The registered crossings of the block design (``block_design.crossings.json``)
are left to the placer, so they can sit on the SLR boundary.
"""

from __future__ import annotations

import collections
import fnmatch
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

NEIGHBOUR_HOPS = 4

Graph = Dict[str, Set[str]]


def _expand_loops(tcl: str) -> List[str]:
    """Lines of a block-design Tcl with its one-level ``for {set v 0} {$v < N} ...`` loops unrolled."""
    lines = tcl.splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"for \{set (\w+) 0\} \{\$\w+ < (\d+)\} \{incr \w+\} \{", lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        var, n = m.group(1), int(m.group(2))
        body = []
        i += 1
        while not lines[i].startswith("}"):
            body.append(lines[i].strip())
            i += 1
        i += 1
        for k in range(n):
            out += [b.replace("${" + var + "}", str(k)) for b in body]
    return out


def block_design_cells(tcl: str) -> Tuple[List[str], Graph]:
    """Cell names of a block-design Tcl and which cells share a net."""
    cells: List[str] = []
    adj: Graph = collections.defaultdict(set)
    for line in _expand_loops(tcl):
        m = re.match(r"create_bd_cell -type \w+ (?:-reference \S+|-vlnv \S+) (\S+)\s*$", line.strip())
        if m:
            cells.append(m.group(1))
        pins = re.findall(r"\[get_bd_pins (\S+?)/\S+?\]", line)
        if line.startswith("connect_bd_net") and len(pins) >= 2:
            for a in pins:
                adj[a].update(b for b in pins if b != a)
    return cells, adj


def cell_modules(tcl: str) -> Dict[str, str]:
    """Cell -> the module (``-reference``) or IP name (``-vlnv``) behind it."""
    out = {}
    for line in _expand_loops(tcl):
        m = re.match(r"create_bd_cell -type \w+ (?:-reference (\S+)|-vlnv (\S+)) (\S+)\s*$", line.strip())
        if m:
            out[m.group(3)] = m.group(1) or m.group(2).split(":")[2]
    return out


def crossing_cells(crossings: Mapping) -> Set[str]:
    return {inst["name"] for c in crossings.get("crossings", []) for inst in c.get("instances", [])}


def link_slrs(ir: Mapping, resolved_io: Mapping) -> Dict[str, int]:
    """Instance -> SLR of the link endpoint feeding it, through the IR's top-level ports."""
    port_slr = {}
    for r in resolved_io.get("detector_inputs", []):
        port = (r.get("output") or {}).get("algo_port")
        if port:
            port_slr[port] = int(r["endpoint"]["slr"])
    design = ir.get("design", ir)
    seeds: Dict[str, int] = {}
    for p in design["top_ports"]:
        if p["direction"] == "in" and p["name"] in port_slr and p.get("instance_id"):
            seeds[p["instance_id"]] = port_slr[p["name"]]

    producers: Graph = collections.defaultdict(set)
    consumers: Graph = collections.defaultdict(set)
    for c in design["connections"]:
        src, dst = c["producer"].get("instance_id"), c["consumer"].get("instance_id")
        if src and dst:
            producers[dst].add(src)
            consumers[src].add(dst)
    # which links' data reaches each instance
    reached_by: Graph = collections.defaultdict(set)
    for seed in seeds:
        todo = [seed]
        while todo:
            for nxt in consumers[todo.pop()]:
                if seed not in reached_by[nxt]:
                    reached_by[nxt].add(seed)
                    todo.append(nxt)
    out = dict(seeds)
    for inst, links in reached_by.items():
        if inst not in out and len(links) == 1 and links <= producers[inst]:
            out[inst] = seeds[next(iter(links))]
    return out


def assign(cells: List[str], adj: Graph, *, slr: Mapping[int, Iterable[str]], links: Mapping[str, int],
           reserved: Iterable[int] = (), skip: Iterable[str] = ()) -> Dict[str, int]:
    """Cell -> SLR: declared patterns, then links, then the neighbours' SLR."""
    skip_set, reserved_set = set(skip), set(reserved)
    res = {c: links[c] for c in cells if c in links}
    for s, patterns in slr.items():
        for pat in patterns:
            for c in cells:
                if fnmatch.fnmatchcase(c, pat):
                    res[c] = int(s)
    for _ in range(NEIGHBOUR_HOPS):
        for c in cells:
            if c in res or c in skip_set:
                continue
            votes = collections.Counter(
                res[n] for n in adj.get(c, ()) if n in res and n not in skip_set and res[n] not in reserved_set)
            if votes:
                res[c] = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    return res


def write_xdc(res: Mapping[str, int], *, root: str, pblock: Optional[str] = None) -> str:
    """Tcl constraints: *root* finds the block-design instance; *pblock* (``{slr}`` in it) makes them hard."""
    lines = [
        "# Written by forge (forge.integration.floorplan); do not edit.",
        "# A placement proposal: SLR assignment of the block-design cells, no coordinates.",
        "# It does not close timing; compare it with the implemented design (floorplan-compare).",
        "",
        f"set _bd [get_cells -quiet -hier -filter {{NAME =~ {root}}}]",
        "if {[llength $_bd] != 1} {",
        f'    error "floorplan: expected one {root}, found [llength $_bd]"',
        "}",
        "set _n 0",
    ]
    by_slr: Dict[int, List[str]] = collections.defaultdict(list)
    for c, s in sorted(res.items()):
        by_slr[s].append(c)
    for s in sorted(by_slr):
        lines += [
            f"# SLR{s}: {len(by_slr[s])} cells",
            f"foreach c {{{' '.join(by_slr[s])}}} {{",
            '    set k [get_cells -quiet "$_bd/$c"]',
            "    if {[llength $k] == 1} {",
            f"        set_property USER_SLR_ASSIGNMENT SLR{s} $k",
        ]
        if pblock:
            # USER_SLR_ASSIGNMENT alone is soft: place_design can leave part of a block elsewhere.
            lines.append(f"        add_cells_to_pblock [get_pblocks {pblock.format(slr=s)}] $k")
        lines += [
            "        incr _n",
            "    } else {",
            '        puts "WARNING: floorplan: $c matched [llength $k] cells"',
            "    }",
            "}",
        ]
    lines.append('puts "floorplan: $_n cells assigned"')
    return "\n".join(lines) + "\n"


def floorplan(block_design: Path, ir: Path, resolved_io: Path, spec: Mapping,
              crossings: Optional[Path] = None) -> Tuple[Dict[str, int], List[str], Graph, Set[str]]:
    """(cell -> SLR, every cell, the cells' nets, the registered crossings) from the
    build's files and a floorplan spec (``slr``, ``reserved``; see :func:`assign`)."""
    cells, adj = block_design_cells(block_design.read_text())
    skip = crossing_cells(json.loads(crossings.read_text())) if crossings and crossings.exists() else set()
    links = link_slrs(json.loads(ir.read_text()), json.loads(resolved_io.read_text()))
    res = assign(cells, adj, slr=spec.get("slr") or {}, links=links,
                 reserved=spec.get("reserved") or (), skip=skip)
    return res, cells, adj, skip
