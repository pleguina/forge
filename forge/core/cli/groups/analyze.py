"""forge analyze — performance analysis, latency checking, reporting, and plotting."""

from __future__ import annotations

import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# hls-report
# ---------------------------------------------------------------------------

def _emit_analyze_envelope(*, status: str, json_mode: bool, **kwargs) -> None:
    """Shared tail for every `forge analyze` subcommand (a lighter-touch
    pass than the pass/fail-gate commands: `metrics`/`artifacts` matter
    more than `diagnostics` here,
    since these are report-generation commands, not verification gates).
    Only prints/exits in JSON mode — non-JSON output is each command's
    own pre-existing narrative prints, left untouched."""
    if not json_mode:
        return
    from forge.core.cli.envelope import CommandEnvelope, emit

    envelope = CommandEnvelope(status=status, **kwargs)
    sys.exit(emit(envelope, json_mode=True))


def cmd_hls_report(args) -> None:
    """Generate HLS synthesis summary from a build_hls tree."""
    from forge.analysis.hls_reports.extractor import collect_reports
    from forge.analysis.hls_reports.formatter import to_csv, to_markdown, to_html

    json_mode = getattr(args, "json", False)
    build_root = Path(args.hls_build_root)
    output_dir = Path(args.output)
    solution   = getattr(args, "solution", "solution1")

    try:
        reports = collect_reports(build_root, solution=solution)
    except FileNotFoundError as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if not reports:
        _emit_analyze_envelope(
            status="fail", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": "No HLS modules found."}],
        )
        print("No HLS modules found.", file=sys.stderr)
        sys.exit(1)

    ok_count  = sum(1 for r in reports if r.status == "ok")
    err_count = sum(1 for r in reports if r.status != "ok")
    if not json_mode:
        print(f"Found {len(reports)} module(s): {ok_count} ok, {err_count} missing/error")

    try:
        to_csv(     reports, output_dir / "hls_summary.csv")
        to_markdown(reports, output_dir / "hls_summary.md")
        to_html(    reports, output_dir / "hls_summary.html")
    except OSError as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR: writing reports: {exc}", file=sys.stderr)
        sys.exit(1)

    _emit_analyze_envelope(
        status="warn" if err_count else "pass", json_mode=json_mode,
        artifacts=[
            str(output_dir / "hls_summary.csv"),
            str(output_dir / "hls_summary.md"),
            str(output_dir / "hls_summary.html"),
        ],
        metrics={"modules_total": len(reports), "modules_ok": ok_count, "modules_error": err_count},
    )
    print(f"Reports written to {output_dir}/")
    print(f"  hls_summary.csv  hls_summary.md  hls_summary.html")


# ---------------------------------------------------------------------------
# latency-check
# ---------------------------------------------------------------------------

def cmd_latency_check(args) -> None:
    """Static latency mismatch checker from design.yml topology."""
    from forge.analysis.latency_static.graph import build_graph
    from forge.analysis.latency_static.checker import check_merge_points
    from forge.analysis.latency_static.reporter import render_markdown

    json_mode = getattr(args, "json", False)
    design_path = Path(args.design)
    output_path = Path(args.output)
    modules_yml = Path(args.contracts_from) if getattr(args, "contracts_from", None) else None

    # Optionally load HLS latency data
    hls_reports: "dict | None" = None
    hls_root = getattr(args, "hls_build_root", None)
    if hls_root:
        hls_root_path = Path(hls_root)
        if hls_root_path.exists():
            from forge.analysis.hls_reports.extractor import collect_reports, latency_map_from_reports
            try:
                raw_reports = collect_reports(hls_root_path)
                hls_reports = latency_map_from_reports(raw_reports)
            except Exception as exc:
                print(f"WARNING: could not load HLS reports: {exc}", file=sys.stderr)

    try:
        graph = build_graph(design_path, modules_yml_path=modules_yml, hls_reports=hls_reports)
    except Exception as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR building latency graph: {exc}", file=sys.stderr)
        sys.exit(1)

    mismatch_reports = check_merge_points(graph)

    try:
        render_markdown(graph, mismatch_reports, output_path)
    except OSError as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR writing report: {exc}", file=sys.stderr)
        sys.exit(1)

    mismatches = [r for r in mismatch_reports if r.is_mismatch]
    unknowns   = [r for r in mismatch_reports if r.has_unknowns]

    _emit_analyze_envelope(
        status="fail" if mismatches else "pass", json_mode=json_mode,
        diagnostics=[
            {
                "severity": "error", "code": None, "category": "latency_mismatch",
                "message": f"{r.merge_node}: delta {r.delta} cycles — {r.suggestion}",
            }
            for r in mismatches
        ],
        artifacts=[str(output_path)],
        metrics={
            "merge_points_checked": len(mismatch_reports),
            "mismatches": len(mismatches),
            "unknowns": len(unknowns),
        },
    )

    if mismatches:
        print(f"⚠ Latency mismatches: {len(mismatches)}", file=sys.stderr)
        for r in mismatches:
            print(f"  {r.merge_node}: Δ{r.delta} cycles — {r.suggestion}", file=sys.stderr)
    if unknowns:
        print(f"  ({len(unknowns)} merge point(s) with unknown latency)")
    if not mismatches:
        print(f"✅ No latency mismatches detected ({len(mismatch_reports)} merge point(s) checked)")

    print(f"Report: {output_path}")
    sys.exit(1 if mismatches else 0)


# ---------------------------------------------------------------------------
# runtime-latency
# ---------------------------------------------------------------------------

def cmd_runtime_latency(args) -> None:
    """Compare HLS-predicted vs simulation-observed latency from probe CSV files."""
    from forge.analysis.latency_runtime.probe import load_probe_csv, load_wide_probe_csv, measure_latency
    from forge.analysis.latency_runtime.comparator import compare
    from forge.analysis.latency_runtime.reporter import render_markdown

    json_mode = getattr(args, "json", False)
    probe_csv  = Path(args.probe_csv)
    output_path = Path(args.output)
    probe_format = getattr(args, "probe_format", "long")
    loader = load_wide_probe_csv if probe_format == "wide" else load_probe_csv

    try:
        events = loader(probe_csv)
    except FileNotFoundError as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Load HLS predictions if available
    hls_map: "dict" = {}
    hls_root = getattr(args, "hls_build_root", None)
    if hls_root and Path(hls_root).exists():
        from forge.analysis.hls_reports.extractor import collect_reports, latency_map_from_reports
        try:
            hls_map = latency_map_from_reports(collect_reports(Path(hls_root)))
        except Exception as exc:
            print(f"WARNING: could not load HLS reports: {exc}", file=sys.stderr)

    # Build comparisons from --probe-pairs (format: "module:in_signal:out_signal")
    comparisons = []
    for pair in (getattr(args, "probe_pairs", None) or []):
        parts = pair.split(":")
        if len(parts) != 3:
            print(f"WARNING: invalid --probe-pairs entry '{pair}' (expected module:in:out)", file=sys.stderr)
            continue
        mod_name, in_sig, out_sig = parts
        observed = measure_latency(events, in_sig, out_sig)
        comparisons.append(compare(mod_name, hls_map.get(mod_name), observed))

    if not comparisons:
        _emit_analyze_envelope(
            status="fail", json_mode=json_mode,
            diagnostics=[{
                "severity": "error",
                "message": "No probe pairs found — pass --probe-pairs module:in_signal:out_signal",
            }],
        )
        print("No probe pairs found — pass --probe-pairs module:in_signal:out_signal", file=sys.stderr)
        sys.exit(1)

    try:
        render_markdown(comparisons, out_path=output_path)
    except OSError as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR writing report: {exc}", file=sys.stderr)
        sys.exit(1)

    _emit_analyze_envelope(
        status="pass", json_mode=json_mode,
        artifacts=[str(output_path)],
        metrics={
            "comparisons": len(comparisons),
            "verdicts": {c.module_name: c.verdict for c in comparisons},
        },
    )
    print(f"Runtime latency report: {output_path}")
    for c in comparisons:
        delta = f"+{c.delta}" if c.delta and c.delta > 0 else str(c.delta or "—")
        print(f"  {c.module_name:25s}  HLS={c.hls_predicted or '—':>4}  "
              f"obs={c.observed or '—':>4}  Δ={delta:>5}  [{c.verdict}]")


# ---------------------------------------------------------------------------
# plot-results
# ---------------------------------------------------------------------------

def cmd_plot_results(args) -> None:
    """Render result comparison plots from a plugin-defined config file."""
    from forge.analysis.result_plots.config import load_plot_config
    from forge.analysis.result_plots.engine import generate_plots

    json_mode = getattr(args, "json", False)
    config_path   = Path(args.config)
    observed_csv  = Path(args.observed)
    output_dir    = Path(args.output)
    reference_csv = Path(args.reference) if getattr(args, "reference", None) else None

    try:
        specs = load_plot_config(config_path)
    except Exception as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR loading plot config: {exc}", file=sys.stderr)
        sys.exit(1)

    if not specs:
        _emit_analyze_envelope(
            status="fail", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": "No plot specs found in config."}],
        )
        print("No plot specs found in config.", file=sys.stderr)
        sys.exit(1)

    try:
        written = generate_plots(
            specs,
            observed_csv=observed_csv,
            output_dir=output_dir,
            reference_csv=reference_csv,
        )
    except Exception as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    _emit_analyze_envelope(
        status="pass", json_mode=json_mode,
        artifacts=[str(p) for p in written],
        metrics={"figures": len(written)},
    )
    print(f"Wrote {len(written)} figure(s) to {output_dir}/")


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------

def cmd_dashboard(args) -> None:
    """Aggregate analysis artifacts into an HTML dashboard."""
    from forge.analysis.dashboards.aggregator import collect
    from forge.analysis.dashboards.renderer import render_html, render_markdown_summary

    json_mode = getattr(args, "json", False)
    reports_dir = Path(args.input)
    output_dir  = Path(args.output)

    aggregated = collect(reports_dir)

    try:
        render_html(            aggregated, output_dir / "dashboard.html")
        render_markdown_summary(aggregated, output_dir / "summary.md")
    except OSError as exc:
        _emit_analyze_envelope(
            status="error", json_mode=json_mode,
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        print(f"ERROR writing dashboard: {exc}", file=sys.stderr)
        sys.exit(1)

    _emit_analyze_envelope(
        status="pass", json_mode=json_mode,
        artifacts=[str(output_dir / "dashboard.html"), str(output_dir / "summary.md")],
    )
    print(f"Dashboard written to {output_dir}/")
    print(f"  dashboard.html  summary.md")


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the ``forge analyze`` sub-parser and all its commands."""
    import argparse

    p_analyze = sub.add_parser(
        "analyze",
        help="Performance analysis, latency checks, and reporting",
        description=(
            "Analysis commands.\n\n"
            "Available sub-commands:\n"
            "  hls-report       Parse HLS synthesis reports and emit CSV/MD/HTML\n"
            "  latency-check    Static latency mismatch checker\n"
            "  runtime-latency  HLS-predicted vs simulation-observed latency\n"
            "  plot-results     Render comparison plots from a config file\n"
            "  dashboard        Aggregate all analysis artifacts into HTML dashboard\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  forge analyze hls-report --hls-build-root build_hls"
            " --output out/reports/hls_summary\n"
            "  forge analyze latency-check plugins/myplugin/designs/design.yml"
            " --contracts-from plugins/myplugin/modules.yml"
            " --hls-build-root build_hls"
            " --output out/reports/latency_check.md\n"
            "  forge analyze runtime-latency"
            " --probe-csv out/probes/probes.csv"
            " --probe-pairs 'mymod:in_valid:out_valid'"
            " --output out/reports/runtime_latency.md\n"
            "  forge analyze plot-results"
            " --config plot_config.yml"
            " --observed observed.csv"
            " --reference expected.csv"
            " --output out/plots\n"
            "  forge analyze dashboard"
            " --input out/reports"
            " --output out/dashboard\n"
        ),
    )

    cmd_sub = p_analyze.add_subparsers(
        dest="analyze_command", metavar="COMMAND", required=True
    )

    # ── hls-report ─────────────────────────────────────────────────────────
    p_hls = cmd_sub.add_parser("hls-report", help="Parse HLS reports → CSV / MD / HTML")
    p_hls.add_argument(
        "--hls-build-root", default="build_hls", metavar="DIR",
        help="HLS build directory (default: build_hls)",
    )
    p_hls.add_argument(
        "--output", "-o", default="out/reports", metavar="DIR",
        help="Output directory (default: out/reports)",
    )
    p_hls.add_argument(
        "--solution", default="solution1", metavar="NAME",
        help="Vitis HLS solution name (default: solution1)",
    )
    p_hls.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_hls.set_defaults(func=cmd_hls_report)

    # ── latency-check ──────────────────────────────────────────────────────
    p_lat = cmd_sub.add_parser(
        "latency-check",
        help="Static latency mismatch detector from design topology",
    )
    p_lat.add_argument("design", metavar="DESIGN_YML",
                       help="Path to design.yml")
    p_lat.add_argument(
        "--contracts-from", metavar="MODULES_YML",
        help="Explicit modules.yml path (default: registry: field in design.yml)",
    )
    p_lat.add_argument(
        "--hls-build-root", metavar="DIR",
        help="HLS build directory — used to load synthesis latency values",
    )
    p_lat.add_argument(
        "--output", "-o", default="out/reports/latency_check.md", metavar="FILE",
        help="Output Markdown file (default: out/reports/latency_check.md)",
    )
    p_lat.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_lat.set_defaults(func=cmd_latency_check)

    # ── runtime-latency ────────────────────────────────────────────────────
    p_rt = cmd_sub.add_parser(
        "runtime-latency",
        help="Compare HLS-predicted vs simulation-observed latency",
    )
    p_rt.add_argument(
        "--probe-csv", required=True, metavar="FILE",
        help="CSV with columns: cycle, signal, value (written by forge verify run)",
    )
    p_rt.add_argument(
        "--probe-format", choices=["long", "wide"], default="long",
        help=(
            "'long' (default): cycle,signal,value rows, one event per row. "
            "'wide': cycle,<probe1>,<probe2>,... — one row per cycle, one "
            "column per probe (the format forge.verification.gen_sim's real "
            "Tier-2 probe emission produces under --probe-log/PROBE_LOG=1)."
        ),
    )
    p_rt.add_argument(
        "--probe-pairs", nargs="+", metavar="MODULE:IN_SIGNAL:OUT_SIGNAL",
        help="One entry per module: module_name:input_valid_signal:output_valid_signal",
    )
    p_rt.add_argument(
        "--hls-build-root", metavar="DIR",
        help="HLS build root — supplies predicted latency from csynth.xml",
    )
    p_rt.add_argument(
        "--output", "-o", default="out/reports/runtime_latency.md", metavar="FILE",
        help="Output Markdown file",
    )
    p_rt.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_rt.set_defaults(func=cmd_runtime_latency)

    # ── plot-results ───────────────────────────────────────────────────────
    p_plot = cmd_sub.add_parser(
        "plot-results",
        help="Render comparison figures from a plugin-defined config",
    )
    p_plot.add_argument(
        "--config", required=True, metavar="FILE",
        help="plot_config.yml — defines plots, column names, and kinds",
    )
    p_plot.add_argument(
        "--observed", required=True, metavar="FILE",
        help="CSV of observed / simulated values",
    )
    p_plot.add_argument(
        "--reference", metavar="FILE",
        help="Optional CSV of reference / expected values",
    )
    p_plot.add_argument(
        "--output", "-o", default="out/plots", metavar="DIR",
        help="Output directory for PNG figures (default: out/plots)",
    )
    p_plot.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_plot.set_defaults(func=cmd_plot_results)

    # ── dashboard ──────────────────────────────────────────────────────────
    p_dash = cmd_sub.add_parser(
        "dashboard",
        help="Aggregate analysis artifacts into HTML dashboard + Markdown summary",
    )
    p_dash.add_argument(
        "--input", "-i", default="out/reports", metavar="DIR",
        help="Reports directory (default: out/reports)",
    )
    p_dash.add_argument(
        "--output", "-o", default="out/dashboard", metavar="DIR",
        help="Output directory (default: out/dashboard)",
    )
    p_dash.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p_dash.set_defaults(func=cmd_dashboard)
