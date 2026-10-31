"""Shared helpers used across all forge CLI group modules."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import List, Tuple

from forge.core.cli.envelope import status_for_exception
from forge.core.utils.path_resolution import consumer_root, resolve_path  # noqa: F401 — CLI modules import them from here


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
# Running commands, project paths
# ---------------------------------------------------------------------------

def report_command_error(exc: Exception) -> int:
    """Report an exception a command raised and return its exit code: 1 when
    it describes the user's input (``status_for_exception``), 2 otherwise."""
    if status_for_exception(exc) == "fail":
        print_cli_error("Error", exc)
        return 1
    print_cli_error(
        "Unexpected error", exc,
        hint=(
            "Check the command arguments and input files, "
            "or re-run with --debug to capture traceback details."
        ),
    )
    return 2


def run_forge_command(argv: List[str], *, quiet: bool) -> Tuple[int, str]:
    """Run a real `forge <argv>` invocation in-process through the full
    parser — the same argv a user would type, so every step here is a
    real command, not a hand-built call into internals.

    Returns ``(exit_code, captured_stdout)``. When *quiet*, the step's own
    output is captured rather than printed, so a command that chains others
    (`forge init`, `forge test`) can report one line per stage — and can
    still show the captured text if that stage fails.
    """
    from forge.core.cli.main import build_parser

    parser = build_parser()
    parsed = parser.parse_args(argv)
    buffer = io.StringIO()
    ctx = contextlib.redirect_stdout(buffer) if quiet else contextlib.nullcontext()
    with ctx:
        try:
            result = parsed.func(parsed)
        except SystemExit as exc:
            return (0 if exc.code is None else int(exc.code)), buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 — reported as `forge` itself would
            return report_command_error(exc), buffer.getvalue()
    return (0 if result is None else int(result)), buffer.getvalue()


def resolve_project_args(args):
    """Point a design-taking command at the ``forge.yml`` project around the
    working directory.

    Fills ``args.design`` and, where the command has them and the user left
    them unset, ``args.contracts_from``, ``args.consumer_root`` and the
    options ``forge.yml``'s ``build:`` sets. Returns the
    :class:`forge.project.resolve.ResolvedProject`; raises what
    :func:`forge.project.resolve.resolve_project` raises.
    """
    from forge.project.resolve import resolve_project

    project = resolve_project(Path.cwd())
    args.design = str(project.design)
    variant = getattr(args, "variant", None)
    if variant:
        from forge.project.variants import variant_design, variant_dir

        args.design = str(variant_design(project.config, project.design, variant))
        if hasattr(args, "output") and args.output is None:
            top = getattr(args, "top_name", None) or "algo_top"
            args.output = str(variant_dir(project.paths, variant) / "generated" / f"{top}.v")
    if getattr(args, "contracts_from", None) is None and project.registry is not None:
        args.contracts_from = str(project.registry)
    if hasattr(args, "consumer_root") and args.consumer_root is None:
        args.consumer_root = str(project.root)
    for name, value in project.build_options().items():
        if hasattr(args, name) and getattr(args, name) is None:
            setattr(args, name, value)
    return project


def remove_path(path: Path, removed: list[Path]) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    removed.append(path)


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
