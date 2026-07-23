"""forge.analyze.dashboards.renderer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Render an :class:`AggregatedReport` into a self-contained HTML dashboard and
a companion Markdown summary.

No external CSS framework or JS library is required — the HTML uses a small
inline stylesheet.  Images are embedded as base64 data-URIs so the HTML file
is portable without a separate assets directory.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Optional

from forge.analyze.dashboards.aggregator import AggregatedReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML_STYLE = """
<style>
  *{box-sizing:border-box}
  body{font-family:sans-serif;margin:0;padding:0;background:#f0f2f5;color:#2c3e50}
  header{background:#2c3e50;color:#fff;padding:1em 2em}
  header h1{margin:0;font-size:1.6em}
  header p{margin:.3em 0 0;font-size:.9em;opacity:.8}
  main{max-width:1200px;margin:2em auto;padding:0 1em}
  section{background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);
          margin-bottom:2em;padding:1.5em}
  section h2{margin-top:0;color:#2c3e50;border-bottom:2px solid #3498db;
             padding-bottom:.3em}
  .plots{display:flex;flex-wrap:wrap;gap:1em}
  .plots img{max-width:100%;border:1px solid #ddd;border-radius:4px}
  .plot-card{flex:1 1 480px}
  table{border-collapse:collapse;width:100%;font-size:.85em}
  th,td{border:1px solid #ccc;padding:5px 9px}
  th{background:#2c3e50;color:#fff}
  tr:nth-child(even){background:#f9f9f9}
  footer{text-align:center;padding:1em;font-size:.8em;color:#999}
  pre{background:#f5f5f5;padding:1em;border-radius:4px;overflow:auto;font-size:.85em}
</style>
"""


def _md_to_html_fragment(md_text: str) -> str:
    """Very minimal Markdown → HTML conversion (headings, tables, bold, code, hr)."""
    lines = md_text.splitlines()
    html_lines = []
    in_table = False
    in_code = False

    def _inline(s: str) -> str:
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s

    for line in lines:
        if line.startswith("```"):
            if in_code:
                html_lines.append("</pre>")
                in_code = False
            else:
                html_lines.append("<pre>")
                in_code = True
            continue
        if in_code:
            html_lines.append(line)
            continue

        if line.startswith("### "):
            if in_table:
                html_lines.append("</table>"); in_table = False
            html_lines.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            if in_table:
                html_lines.append("</table>"); in_table = False
            html_lines.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            if in_table:
                html_lines.append("</table>"); in_table = False
            html_lines.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("> "):
            html_lines.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.startswith("---"):
            html_lines.append("<hr>")
        elif line.startswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if not in_table:
                html_lines.append("<table>"); in_table = True
            if all(re.fullmatch(r"[-: ]+", c) for c in cells):
                continue  # separator row
            tag = "th" if not any("<td>" in ln for ln in html_lines[-5:] if "<t" in ln) else "td"
            row = "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells)
            html_lines.append(f"<tr>{row}</tr>")
        else:
            if in_table:
                html_lines.append("</table>"); in_table = False
            if line.strip():
                html_lines.append(f"<p>{_inline(line)}</p>")
            elif html_lines and html_lines[-1] not in ("", "<br>"):
                html_lines.append("")

    if in_table:
        html_lines.append("</table>")
    return "\n".join(html_lines)


def _read_md_section(path: Optional[Path], fallback: str) -> str:
    if path and path.exists():
        return _md_to_html_fragment(path.read_text())
    return f"<p><em>{fallback}</em></p>"


def _embed_png(png_path: Path) -> str:
    data = base64.b64encode(png_path.read_bytes()).decode()
    return (
        f'<div class="plot-card">'
        f'<h3>{png_path.stem}</h3>'
        f'<img src="data:image/png;base64,{data}" alt="{png_path.stem}">'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_html(report: AggregatedReport, out_path: Path) -> None:
    """Write a self-contained HTML dashboard to *out_path*."""
    hls_html     = _read_md_section(report.hls_summary_md,     "No HLS report found.")
    lat_html     = _read_md_section(report.latency_check_md,   "No latency check found.")
    rt_html      = _read_md_section(report.runtime_latency_md, "No runtime latency report found.")

    plots_html = ""
    if report.plot_pngs:
        plots_html = '<div class="plots">' + "".join(_embed_png(p) for p in report.plot_pngs) + "</div>"
    else:
        plots_html = "<p><em>No plots found.</em></p>"

    html = (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="UTF-8">'
        "<title>FORGE Analysis Dashboard</title>"
        f"{_HTML_STYLE}"
        "</head><body>"
        "<header><h1>FORGE Analysis Dashboard</h1>"
        "<p>Generated by <code>forge analyze dashboard</code></p></header>"
        "<main>"
        f"<section><h2>HLS Synthesis Report</h2>{hls_html}</section>"
        f"<section><h2>Static Latency Check</h2>{lat_html}</section>"
        f"<section><h2>Runtime Latency Comparison</h2>{rt_html}</section>"
        f"<section><h2>Result Plots</h2>{plots_html}</section>"
        "</main>"
        "<footer>forge analyze dashboard — plugin-agnostic framework analysis</footer>"
        "</body></html>"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)


def render_markdown_summary(report: AggregatedReport, out_path: Path) -> None:
    """Write a short Markdown summary (index of available analysis artifacts)."""
    lines = ["# FORGE Analysis Dashboard — Summary", ""]

    def _section(title: str, path: Optional[Path]) -> None:
        lines.append(f"## {title}")
        if path and path.exists():
            lines.extend([f"→ [{path.name}]({path})", ""])
        else:
            lines.extend(["*not available*", ""])

    _section("HLS Synthesis Report",        report.hls_summary_md)
    _section("Static Latency Check",        report.latency_check_md)
    _section("Runtime Latency Comparison",  report.runtime_latency_md)

    lines.append("## Result Plots")
    if report.plot_pngs:
        for p in report.plot_pngs:
            lines.append(f"- [{p.stem}]({p})")
    else:
        lines.append("*no plots*")
    lines += ["", "*Generated by `forge analyze dashboard`*", ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
