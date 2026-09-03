"""forge inspect — canonical resolved-design IR, read-only.

This is the first CLI consumer of ``forge.ir``. It resolves a design's
modules, instances, interfaces, and connections into one canonical model
and can print it, export it as JSON,
or diff it against a previously exported snapshot.

Read-only by default: building the IR never writes ``ip_info.yaml`` or any
other file — the same invariant established for ``topgen gen-top --dry-run``.
The only file this command ever writes is the one explicitly named by
``--emit-ir``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


def _guided_failure(msg: str, *, json_mode: bool) -> "int":
    """Print a one-line guided error the same way every early-exit path in
    this command always has (plain stdout message, `--json` gets a small
    envelope) and return the exit code to use. Kept distinct from
    ``forge.core.cli.envelope.emit``'s stderr-routing convention
    deliberately — this command's guided errors have always gone to
    stdout, and there is no existing reason to move them (unlike
    ``verify doctor``'s diagnostics, which already had a stdout/stderr
    split to preserve)."""
    from forge.core.cli.envelope import CommandEnvelope

    envelope = CommandEnvelope(
        status="fail",
        diagnostics=[{"severity": "error", "message": msg}],
    )
    if json_mode:
        print(json.dumps(envelope.to_dict(), indent=2))
    else:
        print(f"❌ {msg}")
    return envelope.exit_code()


def cmd_inspect(args):
    from forge.core.cli.envelope import CommandEnvelope, emit
    from forge.core.cli.groups.topgen import _compute_maturity_summary
    from forge.ir import (
        build_project_ir_with_match_report, content_hash, diff_projects, to_json_dict,
    )

    design_path = Path(args.design).expanduser().resolve()
    json_mode = getattr(args, "json", False)

    if not design_path.exists():
        sys.exit(_guided_failure(f"Design file not found: {design_path}", json_mode=json_mode))

    try:
        project, cfg, match_report = build_project_ir_with_match_report(
            design_path,
            contracts_from=getattr(args, "contracts_from", None),
            ip_info=getattr(args, "ip_info", None),
            build_dir=getattr(args, "build_dir", None),
            ip_root=getattr(args, "ip_root", None),
            src_root=getattr(args, "src_root", None),
        )
    except Exception as e:  # noqa: BLE001 - surfaced as a clean CLI error
        if getattr(args, "debug", False):
            raise
        sys.exit(_guided_failure(f"Failed to resolve design: {e}", json_mode=json_mode))

    # forge inspect never runs the generator, so the maturity summary's
    # port-accounting fields (open_outputs/tied_inputs/strict_pass) stay
    # honestly absent (None) rather than fabricated. Module/connection/
    # wiring-method counts are knowable pre-generation and always populate.
    ir_hash = content_hash(project)
    maturity = _compute_maturity_summary(
        cfg, match_report, ir_content_hash=ir_hash,
    )

    diagnostics = [_ir_diagnostic_to_dict(d) for d in project.design.diagnostics]
    errors = [d for d in diagnostics if d["severity"] == "error"]
    warnings = [d for d in diagnostics if d["severity"] == "warning"]
    next_actions = _compute_next_actions(diagnostics, maturity)

    if getattr(args, "diff", None):
        diff_path = Path(args.diff).expanduser().resolve()
        if not diff_path.exists():
            sys.exit(_guided_failure(f"--diff file not found: {diff_path}", json_mode=json_mode))
        from forge.ir.deserialize import IrSchemaError, from_json_dict

        try:
            previous, notes = from_json_dict(json.loads(diff_path.read_text()))
        except IrSchemaError as e:
            sys.exit(_guided_failure(str(e), json_mode=json_mode))
        result = diff_projects(previous, project)
        # Reading an older snapshot may have migrated it; say so, because a
        # migrated snapshot legitimately doesn't hash equal to a current one.
        if notes:
            result = {**result, "schema_notes": notes}
        status = "fail" if (not result["hash_equal"] and errors) else "pass"
        envelope = CommandEnvelope(status=status, metrics={"diff": result})
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        _print_diff(result)
        sys.exit(envelope.exit_code())

    if getattr(args, "explain_staleness", None):
        from forge.ir.provenance import build_provenance, explain_staleness, read_provenance

        prov_path = Path(args.explain_staleness).expanduser().resolve()
        if not prov_path.exists():
            sys.exit(_guided_failure(
                f"--explain-staleness file not found: {prov_path}", json_mode=json_mode,
            ))
        previous = read_provenance(prov_path)
        current = build_provenance(project, command_options=_command_options(args))
        result = explain_staleness(previous, current)
        status = "fail" if result.stale else "pass"
        envelope = CommandEnvelope(
            status=status,
            metrics={"explain_staleness": {"stale": result.stale, "reasons": result.reasons}},
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        if result.stale:
            print(f"⚠️  stale — {len(result.reasons)} reason(s):")
            for r in result.reasons:
                print(f"    - {r}")
        else:
            print("✅ fresh — no reason to regenerate")
        sys.exit(envelope.exit_code())

    artifacts: list[str] = []

    if getattr(args, "emit_ir", None):
        out_path = Path(args.emit_ir).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(to_json_dict(project), indent=2, sort_keys=True))
        artifacts.append(str(out_path))
        if not json_mode:
            print(f"✅ IR written to {out_path}")

    if getattr(args, "provenance", None):
        from forge.ir.provenance import build_provenance, write_provenance

        prov_path = Path(args.provenance).expanduser().resolve()
        write_provenance(prov_path, build_provenance(project, command_options=_command_options(args)))
        artifacts.append(str(prov_path))
        if not json_mode:
            print(f"✅ provenance manifest written to {prov_path}")

    # ── static DOT/SVG ────────────────────────────────────────────────
    # `--dot` never requires the real `dot` binary (it's a plain text
    # template); `--svg` does, and fails loudly and specifically when it's
    # not on PATH — it never silently no-ops on an explicit user request.
    if getattr(args, "dot", None) or getattr(args, "svg", None) or getattr(args, "explorer", None):
        from forge.analysis.design_explorer.dot_renderer import dot_available, render_dot, render_svg
        from forge.analysis.design_explorer.graph_model import build_design_graph
        from forge.core.cli._shared import build_explorer_overlay_data

        latency_by_instance, verification_flow_entry_points, open_decisions = (
            build_explorer_overlay_data(design_path, project, args)
        )
        graph = build_design_graph(
            project,
            latency_by_instance=latency_by_instance,
            verification_flow_entry_points=verification_flow_entry_points,
            open_decisions=open_decisions,
            source_roots=[design_path.parent],
        )
        dot_text = render_dot(graph)

        if getattr(args, "explorer", None):
            from forge.analysis.design_explorer.html_renderer import render_explorer_html

            explorer_path = Path(args.explorer).expanduser().resolve()
            render_explorer_html(graph, explorer_path)
            artifacts.append(str(explorer_path))
            if not json_mode:
                print(f"✅ interactive explorer written to {explorer_path}")

        if getattr(args, "dot", None):
            dot_path = Path(args.dot).expanduser().resolve()
            dot_path.parent.mkdir(parents=True, exist_ok=True)
            dot_path.write_text(dot_text)
            artifacts.append(str(dot_path))
            if not json_mode:
                print(f"✅ DOT written to {dot_path}")

        if getattr(args, "svg", None):
            if not dot_available():
                sys.exit(_guided_failure(
                    "Graphviz `dot` binary not found on PATH — install graphviz to render "
                    "--svg (--dot's plain text output never requires it).",
                    json_mode=json_mode,
                ))
            svg_path = Path(args.svg).expanduser().resolve()
            if not render_svg(dot_text, svg_path):
                sys.exit(_guided_failure(f"`dot` failed to render SVG to {svg_path}", json_mode=json_mode))
            artifacts.append(str(svg_path))
            if not json_mode:
                print(f"✅ SVG written to {svg_path}")

    status = "fail" if errors else ("warn" if warnings else "pass")
    envelope = CommandEnvelope(
        status=status,
        diagnostics=diagnostics,
        artifacts=artifacts,
        metrics={
            "content_hash": ir_hash,
            "counts": {
                "modules": len(project.design.modules),
                "instances": len(project.design.instances),
                "connections": len(project.design.connections),
                "clock_domains": len(project.design.clock_domains),
                "reset_domains": len(project.design.reset_domains),
            },
            "maturity": maturity,
        },
        next_actions=next_actions,
    )

    if json_mode:
        sys.exit(emit(envelope, json_mode=True))
    _print_human(project, ir_hash, envelope)
    sys.exit(envelope.exit_code())


def _command_options(args) -> dict:
    """The subset of CLI args that affect IR resolution — recorded in the
    provenance manifest so `--explain-staleness` can tell "you changed an
    input" apart from "you changed how you called forge inspect"."""
    return {
        "contracts_from": getattr(args, "contracts_from", None),
        "ip_info": getattr(args, "ip_info", None),
        "build_dir": getattr(args, "build_dir", None),
        "ip_root": getattr(args, "ip_root", None),
        "src_root": getattr(args, "src_root", None),
    }


def _ir_diagnostic_to_dict(diag) -> dict:
    """Reshape one IR ``DiagnosticReference`` into the envelope's
    diagnostic-dict shape. ``code`` stays
    ``None`` where the IR has none — an honest absence, matching
    ``Diagnostic.to_dict()``'s "omit/None falsy fields" convention rather
    than fabricating a code the IR never assigned."""
    d = {"severity": diag.severity, "message": diag.message, "code": diag.code}
    if diag.object_id:
        d["object_id"] = diag.object_id
    if diag.location and diag.location.file:
        d["path"] = diag.location.file
    return d


def _compute_next_actions(diagnostics: list, maturity: dict) -> list:
    """A small, explicit, testable heuristic mapping from diagnostic
    severity / maturity state to a suggested next step — not a new IR
    field on ``forge/ir/build.py``, since threading an ``action`` hint
    through every ``DiagnosticReference`` construction site would touch
    many call sites for a concern that's CLI-specific, not IR-specific."""
    actions: list = []
    if any(d["severity"] == "error" for d in diagnostics):
        actions.append("Fix the errors above before generating")
    if maturity["modules"]["compat_mode"] > 0:
        actions.append(
            "Add interface contracts or pass --strict at generation time"
        )
    return actions


#: Width every label in `forge inspect`'s human output is padded to, so the
#: colons line up in one column. Previously hand-padded per line, which drifted.
_LABEL_W = 18


def _row(label: str, value) -> str:
    return f"  {label:<{_LABEL_W}}: {value}"


def _print_human(project, ir_hash: str, envelope) -> None:
    d = project.design
    print(f"forge inspect — {d.name}")
    print(_row("IR schema version", project.schema_version))
    print(_row("content hash", ir_hash))
    print(_row("modules", len(d.modules)))
    for m in d.modules:
        flag = "" if m.ports_resolved else "  ⚠️  ports not resolved"
        print(f"    - {m.name} ({m.kind}, top={m.top}, {len(m.interfaces)} interface(s)){flag}")
    print(_row("instances", len(d.instances)))
    print(_row("connections", len(d.connections)))
    print(_row("clock domains", ", ".join(c.name for c in d.clock_domains) or "(none)"))
    print(_row("reset domains", ", ".join(r.name for r in d.reset_domains) or "(none)"))
    if d.diagnostics:
        print(f"  diagnostics ({len(d.diagnostics)}):")
        for diag in d.diagnostics:
            icon = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}.get(diag.severity, "•")
            print(f"    {icon} {diag.message}")
    else:
        print(_row("diagnostics", "(none)"))

    # A raw dict repr is fine in --json and unreadable in a terminal.
    modules_maturity = envelope.metrics.get("maturity", {}).get("modules", {}) or {}
    if modules_maturity:
        total = modules_maturity.get("total", 0)
        driven = modules_maturity.get("contract_driven", 0)
        compat = modules_maturity.get("compat_mode", 0)
        summary = f"{driven}/{total} contract-driven"
        if compat:
            names = ", ".join(modules_maturity.get("compat_mode_names", []))
            summary += f", {compat} in compat mode ({names})"
        print(_row("contract maturity", summary))
    if envelope.next_actions:
        print("  next actions:")
        for action in envelope.next_actions:
            print(f"    - {action}")


def _print_diff(result: dict) -> None:
    for note in result.get("schema_notes", []):
        print(f"ℹ️  {note}")
    if result.get("schema_a") != result.get("schema_b"):
        print(
            f"schema_version: {result.get('schema_a')} → {result.get('schema_b')} "
            "(a schema change moves the content hash on its own)"
        )
    print(f"hash_equal: {result['hash_equal']}")
    print(f"  before: {result['hash_a']}")
    print(f"  after:  {result['hash_b']}")
    for kind in ("instances", "connections"):
        d = result[kind]
        if d["added"] or d["removed"] or d["changed"]:
            print(f"{kind}:")
            if d["added"]:
                print(f"  + added:   {d['added']}")
            if d["removed"]:
                print(f"  - removed: {d['removed']}")
            if d["changed"]:
                print(f"  ~ changed: {d['changed']}")


def register(sub) -> None:
    """Register the top-level ``forge inspect`` command."""
    p = sub.add_parser(
        "inspect",
        help="Resolve a design into the canonical IR and inspect it (read-only)",
    )
    p.add_argument("design", help="Path to design.yml")
    p.add_argument("--contracts-from", help="Path to modules.yml (interface_contract: entries)")
    p.add_argument("--ip-info", help="Path to a pre-built ip_info.yaml (optional)")
    p.add_argument("--build-dir", help="HLS/RTL build root to scan for component.xml (optional)")
    p.add_argument("--ip-root", help="Additional IP repo root to scan (optional)")
    p.add_argument("--src-root", help="Root for resolving relative RTL src paths (optional)")
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.add_argument("--emit-ir", help="Write the resolved IR as JSON to this path")
    p.add_argument("--diff", help="Diff against a previously --emit-ir'd IR JSON file")
    p.add_argument("--provenance", help="Write a content-hash provenance manifest to this path")
    p.add_argument(
        "--explain-staleness",
        help="Compare against a previously --provenance'd manifest and explain why it's stale",
    )
    p.add_argument("--dot", help="Write a deterministic Graphviz DOT rendering of the design (never requires `dot`)")
    p.add_argument("--svg", help="Write an SVG rendering of the design (requires the `dot` binary on PATH)")
    p.add_argument("--explorer", help="Write a self-contained, offline, interactive HTML design explorer")
    p.add_argument(
        "--verify-design",
        help="Path to design.verification.yml, for --dot/--svg/--explorer's "
             "verification-flow-entry-point overlay (used together with --results-json)",
    )
    p.add_argument(
        "--results-json",
        help="Existing versioned results JSON (from a prior forge test run --results-json), "
             "for --dot/--svg/--explorer's verification-flow-entry-point overlay",
    )
    p.set_defaults(func=cmd_inspect)
