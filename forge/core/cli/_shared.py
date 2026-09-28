"""Shared helpers used across all forge CLI group modules."""

from __future__ import annotations

import copy
import os
import shutil
import sys
import traceback
from pathlib import Path

from forge.core.cli.envelope import status_for_exception


# ---------------------------------------------------------------------------
# Debug flag (set by main() from --debug arg)
# ---------------------------------------------------------------------------

_TOPGEN_DEBUG = False


def debug_enabled() -> bool:
    return _TOPGEN_DEBUG or os.environ.get("TOPGEN_DEBUG", "0") == "1"


def set_debug(value: bool) -> None:
    global _TOPGEN_DEBUG
    _TOPGEN_DEBUG = value


# ---------------------------------------------------------------------------
# Error printing
# ---------------------------------------------------------------------------

def print_cli_error(prefix: str, exc: Exception, *, hint: str | None = None) -> None:
    print(f"❌ {prefix}: {exc}", file=sys.stderr)
    if hint:
        print(f"   → {hint}", file=sys.stderr)
    if debug_enabled():
        traceback.print_exc(file=sys.stderr)
    elif status_for_exception(exc) != "fail":
        # Only offer a traceback for errors that might actually be FORGE's
        # fault. Telling someone with a typo in their design.yml to re-run
        # with --debug sends them looking for a bug in the framework.
        print("   Re-run with --debug for traceback details.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def consumer_root(anchor_path: Path | None = None, explicit_root: Path | None = None) -> Path:
    if explicit_root is not None:
        return Path(explicit_root).expanduser().resolve()
    env_root = os.environ.get("TOPGEN_CONSUMER_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    if anchor_path is not None:
        resolved_anchor = Path(anchor_path).expanduser().resolve()
        return resolved_anchor if resolved_anchor.is_dir() else resolved_anchor.parent
    raise ValueError(
        "Cannot determine consumer root. Pass --consumer-root, set "
        "TOPGEN_CONSUMER_ROOT, or provide an input path that can anchor "
        "relative defaults."
    )


def resolve_path(
    path_value: Path | str | None,
    base_root: Path,
    default: Path | str | None = None,
) -> Path | None:
    candidate = path_value if path_value is not None else default
    if candidate is None:
        return None
    path = Path(candidate).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_root / path).resolve()


def remove_path(path: Path, removed: list[Path]) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    removed.append(path)


# ---------------------------------------------------------------------------
# Pattern helpers (used by topgen port filtering and port-map)
# ---------------------------------------------------------------------------

def match_patterns(name: str, patterns: list[str]) -> bool:
    import re as _re
    return any(_re.search(pattern, name) for pattern in patterns or [])


def numeric_suffix_key(name: str) -> tuple[int, str]:
    import re as _re
    match = _re.search(r'_(\d+)(?:_|$)', name)
    return (int(match.group(1)), name) if match else (0, name)


def sort_entries(entries: list[dict], sort_mode: str | None) -> list[dict]:
    if sort_mode == "numeric_suffix":
        return sorted(entries, key=lambda e: numeric_suffix_key(e["name"]))
    return sorted(entries, key=lambda e: e["name"])


def resolve_interface_metadata(interface_metadata: dict | None) -> dict:
    return copy.deepcopy(interface_metadata or {})


# ---------------------------------------------------------------------------
# Verify-flow helpers
# ---------------------------------------------------------------------------

def load_verify_flow_entries(verify_design_path: Path) -> list[tuple[str, str | None]]:
    import yaml as _yaml
    raw = _yaml.safe_load(verify_design_path.read_text()) or {}
    entries: list[tuple[str, str | None]] = []
    for flow in raw.get("flows", []) or []:
        if not isinstance(flow, dict):
            continue
        flow_name = flow.get("name")
        if not flow_name:
            continue
        flow_kind = flow.get("kind")
        entries.append((str(flow_name), str(flow_kind) if flow_kind else None))
    return entries


# ---------------------------------------------------------------------------
# Design-explorer overlay data
# ---------------------------------------------------------------------------

def build_explorer_overlay_data(design_path: Path, project, args):
    """Compute the three overlays ``build_design_graph``
    (``forge.analysis.design_explorer.graph_model``) accepts:
    ``latency_by_instance``, ``verification_flow_entry_points`` and
    ``open_decisions``.

    Shared by both commands' topology/explorer call sites so they can
    never drift on how these overlays are derived. Both are best-effort:
    a broken/missing registry, a missing ``--verify-design``/
    ``--results-json``, or a flow whose entry point doesn't resolve all
    degrade to an honestly empty overlay — never a hard failure of
    topology/explorer rendering, which must keep working with zero
    overlay data exactly as it always has.

    Args:
        design_path: the design.yml path (for the latency-graph rebuild
            — ``forge.analysis.latency_static.graph.build_graph`` takes a
            path, not an already-resolved ``ResolvedProject``).
        project: the already-built ``ResolvedProject`` the caller's own
            ``build_design_graph`` call will use — reused here for the
            verification join, not rebuilt a second time.
        args: the command's parsed argparse namespace — reads
            ``contracts_from``, ``verify_design``, ``results_json``.

    Returns:
        ``(latency_by_instance, verification_flow_entry_points,
        open_decisions)``.
    """
    latency_by_instance: dict = {}
    try:
        from forge.analysis.latency_static.graph import build_graph as build_latency_graph

        contracts_from = getattr(args, "contracts_from", None)
        modules_yml = Path(contracts_from) if contracts_from else None
        lg = build_latency_graph(design_path, modules_yml_path=modules_yml)
        latency_by_instance = {
            n.instance_id: n.latency for n in lg.nodes.values() if n.latency is not None
        }
    except Exception:  # noqa: BLE001
        latency_by_instance = {}

    verification_flow_entry_points: dict = {}
    verify_design_path = getattr(args, "verify_design", None)
    results_json_path = getattr(args, "results_json", None)
    if verify_design_path and results_json_path and Path(results_json_path).exists():
        try:
            import json

            from forge.analysis.design_explorer.verification_join import join_flow_entry_points

            results_payload = json.loads(Path(results_json_path).read_text())
            verification_flow_entry_points = join_flow_entry_points(
                project, verify_design_path=verify_design_path, results_payload=results_payload,
            )
        except Exception:  # noqa: BLE001
            verification_flow_entry_points = {}

    # The authoring overlay: the connections this project has not made, from
    # the same discovery scan `forge check` runs. Only a FORGE project (one
    # with a forge.yml above the design file) has these — a plugin-layout
    # design has no such scan to draw on and gets an empty overlay, which
    # renders exactly as it did before.
    open_decisions: list = []
    try:
        from forge.analysis.design_explorer.authoring_join import (
            open_decisions_for_project,
        )
        from forge.project.paths import find_project_root

        root = find_project_root(design_path)
        if root is not None:
            open_decisions = open_decisions_for_project(
                root, instance_ids=[i.id for i in project.design.instances],
            )
    except Exception:  # noqa: BLE001
        open_decisions = []

    return latency_by_instance, verification_flow_entry_points, open_decisions
