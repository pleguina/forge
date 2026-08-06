"""forge report — orchestrates existing renderers into one report bundle.

This is a thin wrapper, not new rendering logic: every section reuses a
renderer that already exists elsewhere in the codebase —

- maturity/compatibility: `topgen._compute_maturity_summary`/
  `render_maturity_markdown` (shared helper).
- latency check: `forge.analyze.latency_static` (graph/checker/reporter).
- HLS summary: `forge.analyze.hls_reports` (extractor/formatter) — only
  when `--hls-build-root` is given and has real synthesis data; honest
  absence otherwise (passthrough_demo has no HLS build artifacts, so this
  path is real, not synthetic).
- runtime latency: `forge.analyze.latency_runtime` — only when
  `--probe-csv` is given.
- provenance summary: `forge.ir.provenance.render_markdown` — only when
  `--provenance` points at an existing manifest.
- verification results: `forge.verify.junit_xml.render_markdown` — only
  when `--junit-xml` points at a file a prior `forge test run
  --junit-xml` already wrote (reused, not recomputed).
- dashboard.html/summary.md: `forge.analyze.dashboards.aggregator.collect`/
  `renderer.render_html`/`render_markdown_summary`, run over everything
  this command just wrote into the output directory.

- topology: `forge.analyze.design_explorer` — a
  deterministic Graphviz DOT/SVG rendering of the same canonical IR
  the maturity section above already builds, plus the self-contained
  interactive HTML explorer. Degrades gracefully: the DOT artifact
  is always written; SVG is attempted and its absence (the real `dot`
  binary not on PATH) is noted honestly in `next_actions`/diagnostics,
  never silently omitted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def cmd_report(args) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit

    json_mode = getattr(args, "json", False)
    design_path = Path(args.design).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not design_path.exists():
        envelope = CommandEnvelope(
            status="fail",
            diagnostics=[{"severity": "error", "message": f"Design file not found: {design_path}"}],
        )
        sys.exit(emit(envelope, json_mode=json_mode))

    artifacts: List[str] = []
    diagnostics: List[Dict[str, Any]] = []
    metrics: Dict[str, Any] = {}
    next_actions: List[str] = []

    # ── Topology DOT/SVG ──────────────────────────────────────────────────
    try:
        n_edges = _write_topology_report(design_path, args, output_dir)
        artifacts.append(str(output_dir / "topology.dot"))
        artifacts.append(str(output_dir / "topology_explorer.html"))
        metrics["topology_edges"] = n_edges
        svg_path = output_dir / "topology.svg"
        if svg_path.exists():
            artifacts.append(str(svg_path))
        else:
            diagnostics.append({
                "severity": "note",
                "message": "topology.svg omitted — the `dot` binary is not on PATH "
                           "(topology.dot was still written; install graphviz to render SVG)",
            })
            next_actions.append("Install graphviz (`dot` on PATH) to render topology.svg")
    except Exception as exc:  # noqa: BLE001
        diagnostics.append({
            "severity": "warning",
            "message": f"topology report failed: {exc}",
        })

    # ── Maturity / compatibility report (reuses the shared helper) ─────────
    try:
        maturity = _write_maturity_report(design_path, args, output_dir)
        artifacts.append(str(output_dir / "maturity.md"))
        metrics["maturity"] = maturity
    except Exception as exc:  # noqa: BLE001
        diagnostics.append({
            "severity": "warning",
            "message": f"maturity report failed: {exc}",
        })

    # ── Latency check (reuses forge.analyze.latency_static) ────────────────
    try:
        mismatch_count = _write_latency_check(design_path, args, output_dir)
        artifacts.append(str(output_dir / "latency_check.md"))
        metrics["latency_mismatches"] = mismatch_count
        if mismatch_count:
            diagnostics.append({
                "severity": "warning",
                "message": f"{mismatch_count} latency mismatch(es) found — see latency_check.md",
            })
    except Exception as exc:  # noqa: BLE001
        diagnostics.append({
            "severity": "warning",
            "message": f"latency check failed: {exc}",
        })

    # ── HLS summary (honest absence when no build data) ────────────────────
    hls_build_root = getattr(args, "hls_build_root", None)
    if hls_build_root and Path(hls_build_root).exists():
        try:
            n_modules = _write_hls_report(Path(hls_build_root), args, output_dir)
            if n_modules:
                artifacts.extend([
                    str(output_dir / "hls_summary.md"),
                    str(output_dir / "hls_summary.csv"),
                    str(output_dir / "hls_summary.html"),
                ])
                metrics["hls_modules"] = n_modules
            else:
                diagnostics.append({
                    "severity": "note",
                    "message": "no HLS modules found under --hls-build-root",
                })
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({"severity": "warning", "message": f"HLS report failed: {exc}"})
    else:
        diagnostics.append({
            "severity": "note",
            "message": "no --hls-build-root given (or it doesn't exist) — HLS summary omitted",
        })

    # ── Runtime latency (honest absence when no probe data) ────────────────
    probe_csv = getattr(args, "probe_csv", None)
    if probe_csv and Path(probe_csv).exists():
        try:
            n_comparisons = _write_runtime_latency(Path(probe_csv), args, output_dir)
            if n_comparisons:
                artifacts.append(str(output_dir / "runtime_latency.md"))
                metrics["runtime_latency_comparisons"] = n_comparisons
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({"severity": "warning", "message": f"runtime latency report failed: {exc}"})
    else:
        diagnostics.append({
            "severity": "note",
            "message": "no --probe-csv given (or it doesn't exist) — runtime latency report omitted",
        })

    # ── Provenance summary (honest absence, presentation-only) ─────────────
    provenance_path = getattr(args, "provenance", None)
    if provenance_path and Path(provenance_path).exists():
        from forge.ir.provenance import read_provenance
        from forge.ir.provenance import render_markdown as render_provenance_markdown

        manifest = read_provenance(Path(provenance_path))
        (output_dir / "provenance.md").write_text(render_provenance_markdown(manifest))
        artifacts.append(str(output_dir / "provenance.md"))
    else:
        diagnostics.append({
            "severity": "note",
            "message": "no --provenance manifest given (or it doesn't exist) — "
                       "provenance summary omitted (see forge build --provenance)",
        })

    # ── Verification results (reused from a prior `forge test run`) ────────
    # --results-json is preferred when given — it carries
    # backend id, real duration, and waveform/artifact paths that JUnit's
    # schema has no slot for. --junit-xml alone still works unchanged.
    results_json_path = getattr(args, "results_json", None)
    junit_xml_path = getattr(args, "junit_xml", None)
    if results_json_path and Path(results_json_path).exists():
        import json

        from forge.verify.results import render_results_markdown

        payload = json.loads(Path(results_json_path).read_text())
        (output_dir / "verification_results.md").write_text(render_results_markdown(payload))
        artifacts.append(str(output_dir / "verification_results.md"))
    elif junit_xml_path and Path(junit_xml_path).exists():
        from forge.verify.junit_xml import render_markdown as render_junit_markdown

        (output_dir / "verification_results.md").write_text(
            render_junit_markdown(Path(junit_xml_path))
        )
        artifacts.append(str(output_dir / "verification_results.md"))
    else:
        (output_dir / "verification_results.md").write_text(
            "# Verification results\n\n"
            "No verification results yet — run `forge test run --results-json <path>` "
            "(or `--junit-xml <path>`) and pass that path here.\n"
        )
        artifacts.append(str(output_dir / "verification_results.md"))
        next_actions.append("No verification results yet — run `forge test run`")

    # ── Throughput ────────────────────────────────────────────────────────
    try:
        n_throughput = _write_throughput_report(design_path, args, output_dir)
        if n_throughput:
            artifacts.append(str(output_dir / "throughput.md"))
            metrics["throughput_analyses"] = n_throughput
        else:
            diagnostics.append({
                "severity": "note",
                "message": "no --hls-build-root + --module-width (static) or "
                           "--probe-csv + --fifo-probe (runtime) given — throughput report omitted",
            })
    except Exception as exc:  # noqa: BLE001
        diagnostics.append({"severity": "warning", "message": f"throughput report failed: {exc}"})

    # ── CDC verification ─────────────────────────────────────────────────
    cdc_result_json_path = getattr(args, "cdc_result_json", None)
    if cdc_result_json_path and Path(cdc_result_json_path).exists():
        import json

        from forge.verify.cdc_verification_result import render_cdc_verification_markdown

        payload = json.loads(Path(cdc_result_json_path).read_text())
        (output_dir / "cdc_verification.md").write_text(render_cdc_verification_markdown(payload))
        artifacts.append(str(output_dir / "cdc_verification.md"))
        metrics["cdc_crossings"] = len(payload.get("crossings") or [])
    else:
        diagnostics.append({
            "severity": "note",
            "message": "no --cdc-result-json given (or it doesn't exist) — CDC verification "
                       "report omitted (see forge topgen validate --cdc-result-json)",
        })

    # ── Golden-model comparison ─────────────────────────────────────────────
    golden_comparison_json_path = getattr(args, "golden_comparison_json", None)
    if golden_comparison_json_path and Path(golden_comparison_json_path).exists():
        import json

        from forge.verify.golden_comparison_result import render_golden_comparison_markdown

        payload = json.loads(Path(golden_comparison_json_path).read_text())
        (output_dir / "golden_comparison.md").write_text(render_golden_comparison_markdown(payload))
        artifacts.append(str(output_dir / "golden_comparison.md"))
        metrics["golden_comparison_events"] = len(payload.get("events") or [])
    else:
        diagnostics.append({
            "severity": "note",
            "message": "no --golden-comparison-json given (or it doesn't exist) — golden-model "
                       "comparison report omitted (see forge test run --golden-comparison-json)",
        })

    # ── Project attachments (optional, only with --plugin) ──────────────────
    # A project contributes extra report sections (image panels, ownership
    # annotations, dataset/golden-model identity — anything FORGE core has
    # no business understanding) via forge.analyze.dashboards.attachments'
    # registry, populated by the plugin's own bootstrap.py. FORGE core only
    # ever sees a title/kind/path — never the domain semantics behind it.
    plugin_id = getattr(args, "plugin", None)
    if plugin_id:
        try:
            _write_project_attachments(plugin_id, design_path, output_dir, artifacts, diagnostics)
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({"severity": "warning", "message": f"project attachments failed: {exc}"})

    # ── Dashboard (aggregates everything just written) ──────────────────────
    from forge.analyze.dashboards.aggregator import collect
    from forge.analyze.dashboards.renderer import render_html, render_markdown_summary

    aggregated = collect(output_dir)
    render_html(aggregated, output_dir / "dashboard.html")
    render_markdown_summary(aggregated, output_dir / "summary.md")
    artifacts.append(str(output_dir / "dashboard.html"))
    artifacts.append(str(output_dir / "summary.md"))

    has_error = any(d["severity"] == "error" for d in diagnostics)
    has_warning = any(d["severity"] == "warning" for d in diagnostics)
    status = "fail" if has_error else ("warn" if has_warning else "pass")

    envelope = CommandEnvelope(
        status=status,
        diagnostics=diagnostics,
        artifacts=artifacts,
        metrics=metrics,
        next_actions=next_actions,
    )
    sys.exit(emit(envelope, json_mode=json_mode))


def _write_topology_report(design_path: Path, args, output_dir: Path) -> int:
    from forge.analyze.design_explorer.dot_renderer import dot_available, render_dot, render_svg
    from forge.analyze.design_explorer.graph_model import build_design_graph
    from forge.analyze.design_explorer.html_renderer import render_explorer_html
    from forge.core.cli._shared import build_explorer_overlay_data
    from forge.ir import build_project_ir_with_match_report

    project, _cfg, _match_report = build_project_ir_with_match_report(
        design_path,
        contracts_from=getattr(args, "contracts_from", None),
        ip_info=getattr(args, "ip_info", None),
        build_dir=getattr(args, "build_dir", None),
    )
    latency_by_instance, verification_flow_entry_points = build_explorer_overlay_data(
        design_path, project, args,
    )
    graph = build_design_graph(
        project,
        latency_by_instance=latency_by_instance,
        verification_flow_entry_points=verification_flow_entry_points,
        source_roots=[design_path.parent],
    )
    dot_text = render_dot(graph)
    (output_dir / "topology.dot").write_text(dot_text)
    if dot_available():
        render_svg(dot_text, output_dir / "topology.svg")
    render_explorer_html(graph, output_dir / "topology_explorer.html")
    return len(graph.edges)


def _write_maturity_report(design_path: Path, args, output_dir: Path) -> Dict[str, Any]:
    from forge.core.cli.groups.topgen import _compute_maturity_summary, render_maturity_markdown
    from forge.ir import build_project_ir_with_match_report

    _project, cfg, match_report = build_project_ir_with_match_report(
        design_path,
        contracts_from=getattr(args, "contracts_from", None),
        ip_info=getattr(args, "ip_info", None),
        build_dir=getattr(args, "build_dir", None),
    )
    maturity = _compute_maturity_summary(cfg, match_report)
    (output_dir / "maturity.md").write_text(render_maturity_markdown(maturity))
    return maturity


def _write_latency_check(design_path: Path, args, output_dir: Path) -> int:
    from forge.analyze.latency_static.checker import check_merge_points
    from forge.analyze.latency_static.graph import build_graph
    from forge.analyze.latency_static.reporter import render_markdown

    contracts_from = getattr(args, "contracts_from", None)
    modules_yml = Path(contracts_from) if contracts_from else None
    graph = build_graph(design_path, modules_yml_path=modules_yml)
    mismatch_reports = check_merge_points(graph)
    render_markdown(graph, mismatch_reports, output_dir / "latency_check.md")
    return sum(1 for r in mismatch_reports if r.is_mismatch)


def _write_hls_report(hls_build_root: Path, args, output_dir: Path) -> int:
    from forge.analyze.hls_reports.extractor import collect_reports
    from forge.analyze.hls_reports.formatter import to_csv, to_html, to_markdown

    reports = collect_reports(hls_build_root, solution=getattr(args, "solution", "solution1"))
    if not reports:
        return 0
    to_csv(reports, output_dir / "hls_summary.csv")
    to_markdown(reports, output_dir / "hls_summary.md")
    to_html(reports, output_dir / "hls_summary.html")
    return len(reports)


def _write_runtime_latency(probe_csv: Path, args, output_dir: Path) -> int:
    from forge.analyze.latency_runtime.comparator import compare
    from forge.analyze.latency_runtime.probe import (
        load_probe_csv, load_wide_probe_csv, measure_latency,
    )
    from forge.analyze.latency_runtime.reporter import render_markdown

    probe_format = getattr(args, "probe_format", "long")
    loader = load_wide_probe_csv if probe_format == "wide" else load_probe_csv
    events = loader(probe_csv)

    hls_map: Dict[str, Any] = {}
    hls_build_root = getattr(args, "hls_build_root", None)
    if hls_build_root and Path(hls_build_root).exists():
        from forge.analyze.hls_reports.extractor import collect_reports, latency_map_from_reports

        hls_map = latency_map_from_reports(collect_reports(Path(hls_build_root)))

    comparisons = []
    for pair in (getattr(args, "probe_pairs", None) or []):
        parts = pair.split(":")
        if len(parts) != 3:
            continue
        mod_name, in_sig, out_sig = parts
        observed = measure_latency(events, in_sig, out_sig)
        comparisons.append(compare(mod_name, hls_map.get(mod_name), observed))

    if not comparisons:
        return 0
    render_markdown(comparisons, out_path=output_dir / "runtime_latency.md")
    return len(comparisons)


def _write_throughput_report(design_path: Path, args, output_dir: Path) -> int:
    """forge.throughput_result.v1.

    Static side reuses --hls-build-root (already an existing flag) plus
    a new repeatable --module-width name:bits (a port width isn't an HLS-
    report fact, so it's never guessed). Runtime side reuses the
    existing --probe-csv/--probe-format flags plus a new repeatable
    --fifo-probe object_id:full:empty[:occupancy[:overflow]].
    """
    import json

    from forge.verify.throughput_result import THROUGHPUT_RESULT_SCHEMA, ThroughputResult
    from forge.verify.throughput_result import render_throughput_markdown

    static_analyses = []
    bottleneck = None
    predicted_rate = None
    hls_build_root = getattr(args, "hls_build_root", None)
    if hls_build_root and Path(hls_build_root).exists():
        from forge.analyze.hls_reports.extractor import collect_reports
        from forge.analyze.throughput_static.model import build_design_throughput_analysis

        widths: Dict[str, int] = {}
        for entry in (getattr(args, "module_width", None) or []):
            parts = entry.split(":")
            if len(parts) == 2 and parts[1].isdigit():
                widths[parts[0]] = int(parts[1])
        if widths:
            reports = collect_reports(Path(hls_build_root), solution=getattr(args, "solution", "solution1"))
            static_analyses, bottleneck, predicted_rate = build_design_throughput_analysis(reports, widths)

    runtime_results = []
    probe_csv = getattr(args, "probe_csv", None)
    if probe_csv and Path(probe_csv).exists():
        from forge.analyze.throughput_runtime.probe import build_runtime_throughput_result

        for entry in (getattr(args, "fifo_probe", None) or []):
            parts = entry.split(":")
            if len(parts) < 3:
                continue
            object_id, full_sig, empty_sig = parts[0], parts[1], parts[2]
            occupancy_sig = parts[3] if len(parts) > 3 and parts[3] else None
            overflow_sig = parts[4] if len(parts) > 4 and parts[4] else None
            try:
                runtime_results.append(build_runtime_throughput_result(
                    Path(probe_csv), object_id, full_signal=full_sig, empty_signal=empty_sig,
                    occupancy_signal=occupancy_sig, overflow_signal=overflow_sig,
                ))
            except ValueError:
                continue

    if not static_analyses and not runtime_results:
        return 0

    from forge.core.utils.content_hash import hash_file

    result = ThroughputResult(
        schema=THROUGHPUT_RESULT_SCHEMA,
        design_hash=hash_file(design_path),
        static=static_analyses, runtime=runtime_results,
        predicted_rate=predicted_rate, bottleneck=bottleneck,
    )
    payload = result.to_dict()
    (output_dir / "throughput.json").write_text(json.dumps(payload, indent=2) + "\n")
    (output_dir / "throughput.md").write_text(render_throughput_markdown(payload))
    return len(static_analyses) + len(runtime_results)


def _write_project_attachments(
    plugin_id: str, design_path: Path, output_dir: Path,
    artifacts: List[str], diagnostics: List[Dict[str, Any]],
) -> None:
    """Bootstrap *plugin_id* (same mechanism `forge verify run --plugin`
    uses) and collect whatever ReportAttachmentProvider it has registered.

    The plugin's own tools/ directory is derived from *design_path*'s
    real location (``<plugin_root>/forge/designs/x.yml`` ->
    ``<plugin_root>/forge/verify/tools``) rather than requiring a
    separate flag — every reference plugin in this repo follows that
    layout.
    """
    import json
    import sys as _sys

    from forge.analyze.dashboards.attachments import get_report_attachment_providers
    from forge.verify.plugin_registry import bootstrap_plugin

    tools_dir = design_path.parent.parent / "verify" / "tools"
    if tools_dir.is_dir() and str(tools_dir) not in _sys.path:
        _sys.path.insert(0, str(tools_dir))

    try:
        bootstrap_plugin(plugin_id)
    except LookupError:
        import importlib as _il
        try:
            _il.import_module("bootstrap")
            bootstrap_plugin(plugin_id)
        except (ImportError, LookupError) as exc:
            diagnostics.append({
                "severity": "warning",
                "message": f"could not bootstrap plugin {plugin_id!r} for report attachments: {exc}",
            })
            return

    providers = get_report_attachment_providers()
    if not providers:
        diagnostics.append({
            "severity": "note",
            "message": f"plugin {plugin_id!r} bootstrapped but registered no report attachment providers",
        })
        return

    all_attachments = []
    for provider in providers:
        attachments = provider.build_attachments(design_path, output_dir)
        all_attachments.extend(attachments)

    (output_dir / "attachments.json").write_text(
        json.dumps([a.to_dict() for a in all_attachments], indent=2) + "\n"
    )
    artifacts.append(str(output_dir / "attachments.json"))
    for a in all_attachments:
        artifacts.append(str(output_dir / a.path))


def register(sub) -> None:
    """Register the top-level ``forge report`` command."""
    p = sub.add_parser(
        "report",
        help="Generate a self-contained report bundle (maturity, latency, "
             "verification, provenance, dashboard)",
    )
    p.add_argument("design", help="Path to design.yml")
    p.add_argument("--contracts-from", help="Path to modules.yml (interface_contract: entries)")
    p.add_argument("--ip-info", help="Path to a pre-built ip_info.yaml (optional)")
    p.add_argument("--build-dir", help="HLS/RTL build root to scan for component.xml (optional)")
    p.add_argument("--output", "-o", default="report", help="Report output directory (default: ./report)")
    p.add_argument("--hls-build-root", help="build_hls root for the HLS summary section (optional)")
    p.add_argument("--solution", default="solution1", help="HLS solution name (default: solution1)")
    p.add_argument("--probe-csv", help="Probe CSV for the runtime-latency section (optional)")
    p.add_argument("--probe-format", choices=["long", "wide"], default="long")
    p.add_argument(
        "--probe-pairs", action="append",
        help="module:in_signal:out_signal — repeatable, for the runtime-latency section",
    )
    p.add_argument("--provenance", help="Existing provenance.json (from forge build/inspect --provenance)")
    p.add_argument("--junit-xml", help="Existing JUnit XML (from a prior forge test run --junit-xml)")
    p.add_argument(
        "--verify-design",
        help="Path to design.verification.yml, for the topology explorer's "
             "verification-flow-entry-point overlay (used together with --results-json)",
    )
    p.add_argument(
        "--results-json",
        help="Existing versioned results JSON (from a prior forge test run --results-json); "
             "preferred over --junit-xml when both are given",
    )
    p.add_argument(
        "--module-width", action="append",
        help="module_name:bits — repeatable, declared data width for the static "
             "throughput section (a port width isn't an HLS-report fact, never guessed)",
    )
    p.add_argument(
        "--fifo-probe", action="append",
        help="object_id:full_signal:empty_signal[:occupancy_signal[:overflow_signal]] — "
             "repeatable, for the runtime throughput section (uses --probe-csv/--probe-format)",
    )
    p.add_argument(
        "--cdc-result-json",
        help="Existing forge.cdc_verification_result.v1 JSON (from a prior "
             "forge topgen validate --cdc-result-json)",
    )
    p.add_argument(
        "--golden-comparison-json",
        help="Existing forge.golden_comparison_result.v1 JSON (from a prior "
             "forge test run --golden-comparison-json)",
    )
    p.add_argument(
        "--plugin",
        help="Bootstrap this plugin (same mechanism as `forge verify run --plugin`) and "
             "collect any report attachments it has registered via "
             "forge.analyze.dashboards.attachments.register_report_attachment_provider "
             "— e.g. project-owned image panels or ownership annotations",
    )
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.set_defaults(func=cmd_report)
