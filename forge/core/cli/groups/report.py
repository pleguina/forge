"""forge report — orchestrates existing renderers into one report bundle
(release-plan Phase 6, §6.5).

This is a thin wrapper, not new rendering logic: every section reuses a
renderer that already exists elsewhere in the codebase —

- maturity/compatibility: `topgen._compute_maturity_summary`/
  `render_maturity_markdown` (release-plan Phase 6, §6.1's shared helper).
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

- topology: `forge.analyze.design_explorer` (release-plan Phase 8) — a
  deterministic Graphviz DOT/SVG rendering (§8.1) of the same canonical IR
  the maturity section above already builds, plus the self-contained
  interactive HTML explorer (§8.2). Degrades gracefully: the DOT artifact
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

    # ── Topology DOT/SVG (release-plan Phase 8, §8.1) ───────────────────────
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

    # ── Maturity / compatibility report (reuses slice 6.1's helper) ────────
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
    # --results-json (slice 7.2) is preferred when given — it carries
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
    from forge.ir import build_project_ir_with_match_report

    project, _cfg, _match_report = build_project_ir_with_match_report(
        design_path,
        contracts_from=getattr(args, "contracts_from", None),
        ip_info=getattr(args, "ip_info", None),
        build_dir=getattr(args, "build_dir", None),
    )
    graph = build_design_graph(project, source_roots=[design_path.parent])
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
        "--results-json",
        help="Existing versioned results JSON (from a prior forge test run --results-json); "
             "preferred over --junit-xml when both are given",
    )
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.set_defaults(func=cmd_report)
