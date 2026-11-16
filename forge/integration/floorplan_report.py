"""What a floorplan proposes, and what Vivado did with it.

:func:`proposal` describes an SLR assignment from :mod:`forge.integration.floorplan`
before implementation: cells and resources per SLR, the block-design nets that
cross an SLR boundary against a budget, and the spec's own errors. It is a
placement proposal; nothing in it is a timing result.

:func:`compare` reads what implementation produced: the SLR each assigned cell
was placed in (the CSV the Tcl of :func:`check_tcl` writes after
``place_design``), and Vivado's ``report_timing_summary`` and ``report_timing``
text. It reports the cells placed elsewhere than proposed, WNS/TNS, and a slack
histogram of the reported paths by the assigned cell they start or end in.
"""

from __future__ import annotations

import collections
import csv
import fnmatch
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

# SLRs of the stacked-silicon devices FORGE has been used with; anything else
# takes ``slrs`` from the spec.
DEVICE_SLRS = {"xcvu13p": 4, "xcvu9p": 3, "xcku115": 2}
RESOURCES = ("LUT", "FF", "BRAM", "DSP", "URAM")


def device_slrs(spec: Mapping) -> int:
    if "slrs" in spec:
        return int(spec["slrs"])
    part = str(spec.get("part", "")).lower()
    for prefix, n in DEVICE_SLRS.items():
        if part.startswith(prefix):
            return n
    raise ValueError("floorplan: give slrs (the device's SLR count) or a part FORGE knows "
                     f"({', '.join(sorted(DEVICE_SLRS))})")


def check_spec(spec: Mapping, cells: List[str], n_slrs: int) -> List[Dict[str, str]]:
    """The spec's errors and warnings against the block design and the device."""
    issues = []
    owner: Dict[str, int] = {}
    for s, patterns in (spec.get("slr") or {}).items():
        if not 0 <= int(s) < n_slrs:
            issues.append({"level": "error", "message": f"slr {s}: the device has SLRs 0-{n_slrs - 1}"})
        for pat in patterns:
            hits = [c for c in cells if fnmatch.fnmatchcase(c, pat)]
            if not hits:
                issues.append({"level": "warning", "message": f"slr {s}: {pat!r} matches no cell"})
            for c in hits:
                if owner.setdefault(c, int(s)) != int(s):
                    issues.append({"level": "error",
                                   "message": f"{c} is declared in SLR{owner[c]} and SLR{s}"})
    for s in spec.get("reserved") or ():
        if not 0 <= int(s) < n_slrs:
            issues.append({"level": "error", "message": f"reserved {s}: the device has SLRs 0-{n_slrs - 1}"})
    return issues


def proposal(res: Mapping[str, int], cells: List[str], adj: Mapping[str, Set[str]], spec: Mapping, *,
             registered: Iterable[str] = (), resources: Optional[Mapping[str, Mapping[str, int]]] = None
             ) -> Dict[str, Any]:
    """The report of an assignment; *resources* is cell -> {LUT, FF, ...} where known."""
    n_slrs = device_slrs(spec)
    issues = check_spec(spec, cells, n_slrs)
    resources = resources or {}
    per_slr: Dict[int, Dict[str, Any]] = {s: {"cells": 0, "resources": dict.fromkeys(RESOURCES, 0)}
                                          for s in range(n_slrs)}
    for c, s in res.items():
        if s not in per_slr:
            issues.append({"level": "error", "message": f"{c} is assigned to SLR{s}, not on the device"})
            continue
        per_slr[s]["cells"] += 1
        for k, v in (resources.get(c) or {}).items():
            if k in RESOURCES:
                per_slr[s]["resources"][k] += int(v)
    for s, cap in (spec.get("capacity") or {}).items():
        for k, limit in cap.items():
            used = per_slr.get(int(s), {}).get("resources", {}).get(k, 0)
            if used > int(limit):
                issues.append({"level": "error", "message": f"SLR{s}: {used} {k} of a capacity of {limit}"})

    reg = set(registered)
    crossings: Dict[str, int] = collections.Counter()
    unregistered = []
    for a in sorted(adj):
        for b in sorted(adj[a]):
            if a < b and a in res and b in res and res[a] != res[b]:
                key = "-".join(str(x) for x in sorted((res[a], res[b])))
                crossings[key] += 1
                if a not in reg and b not in reg:
                    unregistered.append([a, b])
    budget = spec.get("crossing_budget")
    for key, n in sorted(crossings.items()):
        limit = budget.get(key) if isinstance(budget, Mapping) else budget
        if limit is not None and n > int(limit):
            issues.append({"level": "error", "message": f"SLR {key}: {n} nets cross, the budget is {limit}"})
    if unregistered:
        issues.append({"level": "warning",
                       "message": f"{len(unregistered)} nets cross an SLR boundary without a registered crossing"})
    return {
        "kind": "placement_proposal",
        "timing_closed": False,
        "slrs": n_slrs,
        "per_slr": {str(s): v for s, v in per_slr.items()},
        "unassigned": sorted(set(cells) - set(res)),
        "crossings": {"nets": dict(sorted(crossings.items())), "unregistered": unregistered},
        "issues": issues,
        "assignment": dict(sorted(res.items())),
    }


def hls_resources(metrics: Mapping, cell_modules: Mapping[str, str]) -> Dict[str, Dict[str, int]]:
    """cell -> resources, from an HLS metrics file (``modules: {name: {resources: {used}}}``)."""
    used = {name: (m.get("resources") or {}).get("used") or {} for name, m in (metrics.get("modules") or {}).items()}
    return {c: dict(used[m]) for c, m in cell_modules.items() if m in used}


def check_tcl(res: Mapping[str, int], *, root: str, out_csv: str) -> str:
    """Tcl run after ``place_design``: the SLRs each assigned cell's primitives were placed in."""
    cells = " ".join(sorted(res))
    return "\n".join([
        "# Written by forge (forge.integration.floorplan_report); run after place_design.",
        f"set _bd [get_cells -quiet -hier -filter {{NAME =~ {root}}}]",
        f"set _f [open {out_csv} w]",
        'puts $_f "cell,slrs"',
        f"foreach c {{{cells}}} {{",
        '    set prims [get_cells -quiet -hier -filter "NAME =~ $_bd/$c/* && IS_PRIMITIVE"]',
        "    set slrs [lsort -unique [get_property NAME [get_slrs -quiet -of_objects $prims]]]",
        '    puts $_f "$c,[join $slrs { }]"',
        "}",
        "close $_f",
        "",
    ])


_WNS = re.compile(r"^\s*WNS\(ns\)\s+TNS\(ns\)")
_SLACK = re.compile(r"^\s*Slack(?: \((?:MET|VIOLATED)\))?\s*:\s*(-?[\d.]+|inf)ns")
_POINT = re.compile(r"^\s*(Source|Destination):\s+(\S+)")


def timing_summary(text: str) -> Dict[str, float]:
    """WNS and TNS of a ``report_timing_summary``'s Design Timing Summary."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _WNS.match(line):
            values = lines[i + 2].split()
            return {"wns_ns": float(values[0]), "tns_ns": float(values[1])}
    raise ValueError("no Design Timing Summary (WNS(ns) TNS(ns) ...) in the timing summary")


def timing_paths(text: str) -> List[Dict[str, Any]]:
    """Slack, source and destination of each path of a ``report_timing``."""
    paths: List[Dict[str, Any]] = []
    for line in text.splitlines():
        m = _SLACK.match(line)
        if m:
            paths.append({"slack_ns": float(m.group(1))})
            continue
        m = _POINT.match(line)
        if m and paths:
            paths[-1][m.group(1).lower()] = m.group(2)
    return paths


def _owner(pin: str, cells: Iterable[str]) -> Optional[str]:
    for c in cells:
        if f"/{c}/" in f"/{pin}":
            return c
    return None


def compare(report: Mapping, placed_csv: Path, *, summary: Optional[str] = None,
            paths: Optional[str] = None, bins: Iterable[float] = (-1.0, -0.5, -0.2, 0.0, 0.1, 0.3)
            ) -> Dict[str, Any]:
    """The proposal against the placed design and, when given, its timing reports."""
    assignment = {c: int(s) for c, s in report["assignment"].items()}
    with placed_csv.open() as fh:
        placed = {row["cell"]: [int(x[3:]) for x in row["slrs"].split() if x.startswith("SLR")]
                  for row in csv.DictReader(fh)}
    moved = {c: {"proposed": s, "placed": placed[c]} for c, s in assignment.items()
             if c in placed and placed[c] != [s]}
    out: Dict[str, Any] = {
        "kind": "placement_comparison",
        "cells": len(assignment),
        "placed_as_proposed": sum(1 for c, s in assignment.items() if placed.get(c) == [s]),
        "moved": moved,
        "not_found": sorted(c for c in assignment if c not in placed or not placed[c]),
    }
    if summary is not None:
        out["timing"] = timing_summary(summary)
    if paths is not None:
        edges = sorted(bins)
        hist: Dict[str, int] = collections.Counter()
        by_cell: Dict[str, List[float]] = collections.defaultdict(list)
        for p in timing_paths(paths):
            slack = p["slack_ns"]
            label = next((f"< {e}" for e in edges if slack < e), f">= {edges[-1]}")
            hist[label] += 1
            for end in ("source", "destination"):
                c = _owner(p.get(end, ""), assignment)
                if c:
                    by_cell[c].append(slack)
        out["slack_histogram_ns"] = {k: hist[k] for k in [f"< {e}" for e in edges] + [f">= {edges[-1]}"]}
        out["worst_slack_by_cell_ns"] = dict(sorted(((c, min(v)) for c, v in by_cell.items()), key=lambda kv: kv[1]))
    return out
