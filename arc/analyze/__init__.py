"""arc.analyze — performance analysis, latency checking, reporting, and plotting.

Subsystem responsibilities
--------------------------
hls_reports/
    Parse Vitis HLS csynth.xml synthesis reports and emit CSV / Markdown / HTML
    summaries. Wraps arc.hls.extract_hls_metrics so the XML parsing logic lives
    in exactly one place.

latency_static/
    Topology-based latency checker. Reads design.yml + modules.yml + optional HLS
    reports and detects latency mismatches at every merge point in the pipeline
    DAG. Suggests signal_delay depths where needed.

latency_runtime/
    Compare HLS-predicted latency against simulation-observed latency. Reads
    probe CSV files written during arc verify runs and produces a side-by-side
    comparison table.

result_plots/
    Generic plotting engine. The plugin supplies a plot_config.yml that names
    columns and plot kinds; the framework produces the figures. No OMTF-specific
    semantics here.

dashboards/
    Aggregates the outputs of the other four modules and renders a self-contained
    HTML dashboard plus a Markdown summary.

All five modules are plugin-agnostic — no OMTF or detector-specific assumptions
belong here. Plugin-specific semantics live under plugins/<plugin>/.
"""
