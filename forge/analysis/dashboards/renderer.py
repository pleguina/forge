"""forge.analysis.dashboards.renderer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Render an :class:`AggregatedReport` into a self-contained HTML dashboard and
a companion Markdown summary.

No external CSS framework or JS library is required — the HTML uses a small
inline stylesheet.  Images are embedded as base64 data-URIs so the HTML file
is portable without a separate assets directory.
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Optional

from forge.analysis.dashboards.aggregator import AggregatedReport


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


def _topology_section(report: AggregatedReport) -> str:
    """Static SVG + a relative link to the interactive explorer — never an
    iframe, so the section degrades cleanly if the explorer HTML is opened
    in a context that doesn't support it (see forge report --plugin's own
    topology step, which writes both files unconditionally alongside
    dashboard.html).
    """
    if not report.topology_svg and not report.topology_explorer_html:
        return ""

    body = ""
    if report.topology_svg and report.topology_svg.exists():
        data = base64.b64encode(report.topology_svg.read_bytes()).decode()
        body += f'<img src="data:image/svg+xml;base64,{data}" alt="Design topology">'
    if report.topology_explorer_html and report.topology_explorer_html.exists():
        body += (
            f'<p><a href="{report.topology_explorer_html.name}" target="_blank">'
            "Open interactive topology explorer &rarr;</a></p>"
        )
    return f"<section><h2>Design Topology</h2>{body}</section>"


def _embed_png(png_path: Path) -> str:
    data = base64.b64encode(png_path.read_bytes()).decode()
    return (
        f'<div class="plot-card">'
        f'<h3>{png_path.stem}</h3>'
        f'<img src="data:image/png;base64,{data}" alt="{png_path.stem}">'
        f"</div>"
    )


_IMAGE_MIME = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _embed_attachment(attachment, base_dir: Path) -> str:
    """Render one :class:`~forge.analysis.dashboards.attachments.ReportAttachment`
    generically — this function has no idea what a project's attachment
    *means*, only its declared ``kind``.
    """
    path = base_dir / attachment.path
    title = f"<h3>{attachment.title}</h3>"
    caption = f"<p>{attachment.description}</p>" if attachment.description else ""

    if attachment.kind == "image":
        if not path.is_file():
            return f'<div class="plot-card">{title}<p><em>missing: {attachment.path}</em></p></div>'
        mime = _IMAGE_MIME.get(path.suffix.lower(), "application/octet-stream")
        data = base64.b64encode(path.read_bytes()).decode()
        return (
            f'<div class="plot-card">{title}'
            f'<img src="data:{mime};base64,{data}" alt="{attachment.title}">'
            f"{caption}</div>"
        )
    if attachment.kind == "markdown":
        body = _read_md_section(path if path.is_file() else None, f"missing: {attachment.path}")
        return f'<div class="plot-card">{title}{body}{caption}</div>'
    if attachment.kind == "html":
        body = path.read_text() if path.is_file() else f"<p><em>missing: {attachment.path}</em></p>"
        return f'<div class="plot-card">{title}{body}{caption}</div>'
    return f'<div class="plot-card">{title}<p><em>unknown attachment kind: {attachment.kind}</em></p></div>'


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

    topology_section = _topology_section(report)

    attachments_section = ""
    if report.attachments:
        base_dir = report.attachments_dir or Path(".")
        attachments_html = '<div class="plots">' + "".join(
            _embed_attachment(a, base_dir) for a in report.attachments
        ) + "</div>"
        attachments_section = f"<section><h2>Project Attachments</h2>{attachments_html}</section>"

    html = (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="UTF-8">'
        "<title>FORGE Analysis Dashboard</title>"
        f"{_HTML_STYLE}"
        "</head><body>"
        "<header><h1>FORGE Analysis Dashboard</h1>"
        "<p>Generated by <code>forge analyze dashboard</code></p></header>"
        "<main>"
        f"{topology_section}"
        f"<section><h2>HLS Synthesis Report</h2>{hls_html}</section>"
        f"<section><h2>Static Latency Check</h2>{lat_html}</section>"
        f"<section><h2>Runtime Latency Comparison</h2>{rt_html}</section>"
        f"<section><h2>Result Plots</h2>{plots_html}</section>"
        f"{attachments_section}"
        "</main>"
        "<footer>forge analyze dashboard — plugin-agnostic framework analysis</footer>"
        "</body></html>"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)


def render_markdown_summary(report: AggregatedReport, out_path: Path) -> None:
    """Write a short Markdown summary (index of available analysis artifacts).

    Every link is written *relative to the summary itself* so the report
    directory stays a self-contained, movable bundle — absolute paths baked
    into the Markdown break the moment it's copied, published, or opened on
    another machine. Paths that genuinely live outside the bundle (project
    attachments pointing elsewhere) keep their absolute form rather than a
    long `../../..` chain.
    """
    lines = ["# FORGE Analysis Dashboard — Summary", ""]
    base = out_path.parent

    def _link(path: Path) -> str:
        """Bundle-relative link target, or the absolute path if it escapes."""
        try:
            rel = os.path.relpath(path, base)
        except ValueError:  # different drive on Windows
            return str(path)
        return str(path) if rel.startswith("..") else rel.replace(os.sep, "/")

    def _section(title: str, path: Optional[Path]) -> None:
        lines.append(f"## {title}")
        if path and path.exists():
            lines.extend([f"→ [{path.name}]({_link(path)})", ""])
        else:
            lines.extend(["*not available*", ""])

    lines.append("## Design Topology")
    if report.topology_svg or report.topology_explorer_html:
        if report.topology_svg:
            lines.append(f"→ [{report.topology_svg.name}]({report.topology_svg.name})")
        if report.topology_explorer_html:
            lines.append(f"→ [{report.topology_explorer_html.name}]({report.topology_explorer_html.name}) (interactive)")
    else:
        lines.append("*not available*")
    lines.append("")

    _section("HLS Synthesis Report",        report.hls_summary_md)
    _section("Static Latency Check",        report.latency_check_md)
    _section("Runtime Latency Comparison",  report.runtime_latency_md)

    lines.append("## Result Plots")
    if report.plot_pngs:
        for p in report.plot_pngs:
            lines.append(f"- [{p.stem}]({_link(p)})")
    else:
        lines.append("*no plots*")
    lines.append("")

    lines.append("## Project Attachments")
    if report.attachments:
        for a in report.attachments:
            lines.append(f"- **{a.title}** ({a.kind}): {a.path}" + (f" — {a.description}" if a.description else ""))
    else:
        lines.append("*none*")

    lines += ["", "*Generated by `forge analyze dashboard`*", ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
