"""Self-contained, offline, interactive HTML design explorer (release-plan
Phase 8, §8.2-§8.4).

Follows the same self-contained-HTML pattern already twice-established in
this codebase (``forge/analyze/dashboards/renderer.py``,
``forge/analyze/hls_reports/formatter.py``): plain Python string
concatenation, one inline ``_HTML_STYLE`` constant, zero
``<script src=...>``/``<link href=...>``/external-CDN references. The one
genuinely new thing here is vendoring Cytoscape.js *core* — see
``vendor/README.md`` for what/why/how.

The graph data is embedded as a ``<script type="application/json">`` data
island (escaped via ``escaping.json_script_safe`` so an embedded literal
``</script>`` in project-authored text — a module name, a diagnostic
message — can never terminate the element early). All interactivity
(pan/zoom, search, collapse/expand, filters, overlays, diagnostics-only
view, path-to/from-node, physical-port expansion, the selected-object
details panel) is one inline ``<script>`` reading that data island —
no server round-trip, fully functional offline.
"""
from __future__ import annotations

import dataclasses
import importlib.resources
import json
from pathlib import Path

from .escaping import html_attr, html_text, json_script_safe
from .graph_model import DesignGraph

_HTML_STYLE = """
<style>
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{font-family:-apple-system,Helvetica,Arial,sans-serif;color:#1c2733;display:flex;flex-direction:column}
  header{background:#1c2733;color:#fff;padding:.6em 1em;display:flex;align-items:center;gap:1em;flex-wrap:wrap}
  header h1{font-size:1.1em;margin:0}
  header .meta{font-size:.75em;opacity:.7}
  #layout{flex:1;display:flex;min-height:0}
  #sidebar{width:280px;border-right:1px solid #ddd;padding:.75em;overflow-y:auto;font-size:.85em;background:#fafbfc}
  #sidebar h2{font-size:.8em;text-transform:uppercase;letter-spacing:.04em;color:#5a6b7a;margin:1em 0 .4em}
  #sidebar h2:first-child{margin-top:0}
  #sidebar label{display:block;margin:.15em 0;cursor:pointer}
  #sidebar input[type=text]{width:100%;padding:.3em;margin-bottom:.3em;border:1px solid #ccc;border-radius:4px}
  #sidebar select{width:100%;padding:.25em}
  #cy{flex:1;min-width:0}
  #details{width:340px;border-left:1px solid #ddd;padding:.75em;overflow-y:auto;font-size:.82em;background:#fafbfc}
  #details h2{font-size:1em;margin:.2em 0}
  #details table{width:100%;border-collapse:collapse;margin:.4em 0}
  #details td{padding:2px 4px;vertical-align:top;border-bottom:1px solid #eee}
  #details td:first-child{color:#5a6b7a;white-space:nowrap;width:38%}
  #details .diag{padding:.3em;border-radius:4px;margin:.25em 0;font-size:.85em}
  #details .diag.error{background:#fdecea;color:#611a15}
  #details .diag.warning{background:#fff4e5;color:#663c00}
  #details .diag.info{background:#e8f1fb;color:#0d3c6e}
  .empty-hint{color:#8a97a3;font-style:italic}
  button.small{font-size:.78em;padding:.25em .5em;border:1px solid #ccc;border-radius:4px;background:#fff;cursor:pointer}
  button.small:hover{background:#eef2f7}
  .legend-swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:.35em}
</style>
"""

_HTML_SHELL = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
{style}
</head>
<body>
<header>
  <h1>forge design explorer &mdash; {design_name}</h1>
  <span class="meta">schema {schema_name} {schema_version} &middot; IR {ir_schema_version} &middot; {node_count} nodes &middot; {edge_count} edges</span>
</header>
<div id="layout">
  <div id="sidebar">{sidebar}</div>
  <div id="cy"></div>
  <div id="details"><p class="empty-hint">Select a module, instance, or connection to see details.</p></div>
</div>
<script type="application/json" id="design-graph-data">{data_json}</script>
<script>{cytoscape_js}</script>
<script>{app_js}</script>
</body>
</html>
"""

_SIDEBAR_HTML = """
<h2>Search</h2>
<input type="text" id="search-box" placeholder="module, instance, connection...">

<h2>Grouping</h2>
<label><input type="checkbox" id="toggle-domain-grouping"> Group by clock/reset domain</label>

<h2>Overlay</h2>
<select id="overlay-select">
  <option value="none">None</option>
  <option value="latency">Latency</option>
  <option value="maturity">Contract maturity</option>
  <option value="verification">Verification flow entry points</option>
</select>

<h2>Filters</h2>
<div id="wiring-filters"></div>
<label><input type="checkbox" id="toggle-diagnostics-only"> Diagnostics only</label>

<h2>Path</h2>
<button class="small" id="path-predecessors" disabled>Highlight upstream of selection</button>
<button class="small" id="path-successors" disabled>Highlight downstream of selection</button>
<button class="small" id="path-clear">Clear path highlight</button>

<h2>Legend</h2>
<div id="legend" style="font-size:.85em;line-height:1.6em;"></div>
"""


def _read_vendored_cytoscape_js() -> str:
    """Read the vendored Cytoscape.js core build via ``importlib.resources``
    — never from a hardcoded filesystem path, so this works identically
    from the source tree and from an installed package artifact."""
    # `vendor/` is a plain data subdirectory, not itself an importable
    # package (no __init__.py) — resolved via the real package's own
    # Traversable root instead, which works identically from the source
    # tree and from an installed package artifact.
    resource = importlib.resources.files("forge.analyze.design_explorer").joinpath(
        "vendor", "cytoscape.min.js",
    )
    return resource.read_text(encoding="utf-8")


def render_explorer_html(graph: DesignGraph, out_path: "str | Path") -> None:
    """Render *graph* as a self-contained, offline, interactive HTML file
    at *out_path*."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    node_count = len(graph.nodes)
    edge_count = len(graph.edges)

    payload = dataclasses.asdict(graph)
    data_json = json_script_safe(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    html = _HTML_SHELL.format(
        title=html_attr("forge design explorer"),
        style=_HTML_STYLE,
        design_name=html_text(_design_label(graph)),
        schema_name=html_text(graph.schema.name),
        schema_version=html_text(graph.schema.version),
        ir_schema_version=html_text(graph.source_ir_schema_version),
        node_count=node_count,
        edge_count=edge_count,
        sidebar=_SIDEBAR_HTML,
        data_json=data_json,
        cytoscape_js=_read_vendored_cytoscape_js(),
        app_js=_APP_JS,
    )
    out_path.write_text(html)


def _design_label(graph: DesignGraph) -> str:
    """A short, stable label for the header — ``DesignGraph`` itself
    carries no design *name* field (by design: it's a pure IR projection,
    and ``ResolvedDesign.name`` is a bare design-file stem, not something
    worth re-threading through the whole graph model) — the real, stable
    IR content hash prefix is a meaningful, honest substitute."""
    return graph.source_ir_content_hash[:12]


_APP_JS = r"""
(function () {
  "use strict";
  const GRAPH = JSON.parse(document.getElementById("design-graph-data").textContent);

  const OBJECTS = {};
  GRAPH.objects.forEach(function (o) { OBJECTS[o.kind + "|" + o.id] = o; });
  function findObject(kind, id) {
    return OBJECTS[kind + "|" + id];
  }

  const SEVERITY_RANK = { error: 2, warning: 1, info: 0 };
  const MATURITY_COLOR = { contract: "#2e7d32", mixed: "#ef6c00", compatibility: "#c62828", unknown: "#9e9e9e" };
  const WIRING_COLOR = {
    contract_wiring: "#2e7d32", port_map_ranges: "#1565c0", port_map: "#1565c0",
    auto_match: "#ef6c00", topology_group: "#6a1b9a", heuristic: "#c62828",
  };

  function nodeElement(n) {
    const data = {
      id: n.id, kind: n.kind, label: n.label, module: n.module,
      clockDomain: n.clock_domain, resetDomain: n.reset_domain,
      members: n.members || [], latency: n.latency, maturity: n.maturity,
      diagnostics: n.diagnostics || [], inheritedDiagnostics: n.inherited_diagnostics || [],
      objectId: n.object_id, defaultParent: n.parent || null,
    };
    if (n.parent) data.parent = n.parent;
    return { data: data, classes: "kind-" + n.kind };
  }

  function edgeElement(e) {
    return {
      data: {
        id: e.id, source: e.source, target: e.target,
        wiringMethod: e.wiring_method, crossesClock: e.crosses_clock_domain,
        crossesReset: e.crosses_reset_domain, transformations: e.transformations || [],
        diagnostics: e.diagnostics || [], objectId: e.object_id,
      },
    };
  }

  const elements = GRAPH.nodes.filter(function (n) { return n.kind !== "domain-group"; }).map(nodeElement)
    .concat(GRAPH.nodes.filter(function (n) { return n.kind === "domain-group"; }).map(nodeElement))
    .concat(GRAPH.edges.map(edgeElement));

  const cy = cytoscape({
    container: document.getElementById("cy"),
    elements: elements,
    style: [
      { selector: "node", style: {
          "label": "data(label)", "font-size": 9, "text-wrap": "wrap",
          "background-color": "#eef2f7", "border-width": 1, "border-color": "#90a4ae",
          "shape": "round-rectangle", "padding": "6px", "text-valign": "center",
      } },
      { selector: "node.kind-external-port", style: { "shape": "cut-rectangle", "background-color": "#fff8e1" } },
      { selector: "node.kind-module-group, node.kind-domain-group", style: {
          "shape": "round-rectangle", "background-opacity": 0.06, "border-width": 1.5,
          "border-color": "#5a6b7a", "text-valign": "top", "font-weight": "bold", "padding": "14px",
      } },
      { selector: "node.kind-domain-group", style: { "display": "none" } },
      { selector: "node.dimmed, edge.dimmed", style: { "opacity": 0.15 } },
      { selector: "node.search-match", style: { "border-color": "#d81b60", "border-width": 3 } },
      { selector: "node.path-highlight", style: { "background-color": "#fff3cd", "border-color": "#d81b60", "border-width": 2 } },
      { selector: "node.collapsed-hidden, edge.collapsed-hidden", style: { "display": "none" } },
      { selector: "node.diag-error", style: { "border-color": "#c62828", "border-width": 3 } },
      { selector: "node.diag-warning", style: { "border-color": "#ef6c00", "border-width": 3 } },
      { selector: "edge", style: {
          "width": 1.6, "curve-style": "bezier", "target-arrow-shape": "triangle",
          "target-arrow-color": "#5a6b7a", "line-color": "#90a4ae", "font-size": 8, "label": "",
      } },
      { selector: "edge.path-highlight", style: { "line-color": "#d81b60", "target-arrow-color": "#d81b60", "width": 3 } },
      { selector: "edge.aggregate-edge", style: {
          "line-style": "dashed", "line-color": "#5a6b7a", "target-arrow-color": "#5a6b7a",
          "label": "data(label)",
      } },
      { selector: "edge.filtered-out, node.filtered-out", style: { "display": "none" } },
    ],
    layout: { name: "breadthfirst", directed: true, spacingFactor: 1.1 },
    wheelSensitivity: 0.2,
  });

  GRAPH.edges.forEach(function (e) {
    const wm = e.wiring_method;
    if (wm && WIRING_COLOR[wm]) {
      cy.$id(e.id).style({ "line-color": WIRING_COLOR[wm], "target-arrow-color": WIRING_COLOR[wm] });
    }
  });
  cy.nodes().forEach(function (n) {
    const diags = (n.data("diagnostics") || []).concat(n.data("inheritedDiagnostics") || []);
    if (diags.length) {
      const worst = diags.reduce(function (acc, d) {
        return (SEVERITY_RANK[d.severity] || 0) > (SEVERITY_RANK[acc] || 0) ? d.severity : acc;
      }, "info");
      n.addClass("diag-" + worst);
    }
  });

  // ── Collapse/expand (Defect 5, Option A) ────────────────────────────
  const collapsedGroups = new Set();

  function isHiddenDescendant(node) {
    let p = node.parent();
    while (p && p.length) {
      if (collapsedGroups.has(p.id())) return true;
      p = p.parent();
    }
    return false;
  }

  function collapseGroup(groupId) {
    if (collapsedGroups.has(groupId)) return;
    collapsedGroups.add(groupId);
    const group = cy.$id(groupId);
    const descendants = group.children();
    const descendantIds = new Set(descendants.map(function (n) { return n.id(); }));
    descendants.addClass("collapsed-hidden");
    group.addClass("collapsed");

    const aggCounts = {};
    cy.edges().forEach(function (edge) {
      if (edge.hasClass("aggregate-edge")) return;
      const s = edge.source().id(), t = edge.target().id();
      const sHidden = descendantIds.has(s), tHidden = descendantIds.has(t);
      if (!sHidden && !tHidden) return;
      edge.addClass("collapsed-hidden");
      if (sHidden && tHidden) return;
      const newSrc = sHidden ? groupId : s;
      const newDst = tHidden ? groupId : t;
      if (newSrc === newDst) return;
      const key = newSrc + "->" + newDst;
      aggCounts[key] = (aggCounts[key] || 0) + 1;
    });
    Object.keys(aggCounts).forEach(function (key) {
      const parts = key.split("->");
      const count = aggCounts[key];
      cy.add({
        data: {
          id: "agg:" + groupId + ":" + key, source: parts[0], target: parts[1],
          label: count + " connection" + (count > 1 ? "s" : ""),
        },
        classes: "aggregate-edge",
      });
    });
  }

  function expandGroup(groupId) {
    if (!collapsedGroups.has(groupId)) return;
    collapsedGroups.delete(groupId);
    cy.edges().filter(function (e) { return e.id().indexOf("agg:" + groupId + ":") === 0; }).remove();
    const group = cy.$id(groupId);
    group.children().removeClass("collapsed-hidden");
    group.removeClass("collapsed");
    cy.edges(".collapsed-hidden").forEach(function (edge) {
      if (!isHiddenDescendant(edge.source()) && !isHiddenDescendant(edge.target())) {
        edge.removeClass("collapsed-hidden");
      }
    });
  }

  cy.on("tap", "node.kind-module-group, node.kind-domain-group", function (evt) {
    const id = evt.target.id();
    if (collapsedGroups.has(id)) expandGroup(id); else collapseGroup(id);
  });

  // ── Clock/reset-domain grouping toggle ──────────────────────────────
  let domainGroupingActive = false;
  document.getElementById("toggle-domain-grouping").addEventListener("change", function (evt) {
    domainGroupingActive = evt.target.checked;
    cy.nodes('[kind = "module-group"]').style("display", domainGroupingActive ? "none" : "element");
    cy.nodes('[kind = "domain-group"]').style("display", domainGroupingActive ? "element" : "none");
    cy.nodes('[kind = "instance"]').forEach(function (n) {
      if (domainGroupingActive) {
        const dom = n.data("clockDomain");
        const domainId = dom ? "domain:clock:" + dom : null;
        if (domainId && cy.$id(domainId).length) n.move({ parent: domainId });
      } else {
        n.move({ parent: n.data("defaultParent") || null });
      }
    });
    cy.layout({ name: "breadthfirst", directed: true, spacingFactor: 1.1 }).run();
  });

  // ── Search ───────────────────────────────────────────────────────────
  document.getElementById("search-box").addEventListener("input", function (evt) {
    const q = evt.target.value.trim().toLowerCase();
    cy.nodes().removeClass("search-match dimmed");
    if (!q) return;
    cy.nodes().forEach(function (n) {
      const hay = (n.id() + " " + (n.data("label") || "") + " " + (n.data("module") || "")).toLowerCase();
      if (hay.indexOf(q) !== -1) n.addClass("search-match"); else n.addClass("dimmed");
    });
  });

  // ── Filters (wiring method + diagnostics-only) ──────────────────────
  const wiringMethods = Array.from(new Set(GRAPH.edges.map(function (e) { return e.wiring_method || "(none)"; }))).sort();
  const filterBox = document.getElementById("wiring-filters");
  const disabledMethods = new Set();
  wiringMethods.forEach(function (m) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", function () {
      if (cb.checked) disabledMethods.delete(m); else disabledMethods.add(m);
      applyFilters();
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + m));
    filterBox.appendChild(label);
  });

  let diagnosticsOnly = false;
  document.getElementById("toggle-diagnostics-only").addEventListener("change", function (evt) {
    diagnosticsOnly = evt.target.checked;
    applyFilters();
  });

  function applyFilters() {
    cy.edges().removeClass("filtered-out");
    cy.edges().forEach(function (edge) {
      const wm = edge.data("wiringMethod") || "(none)";
      let hide = disabledMethods.has(wm);
      if (diagnosticsOnly && (edge.data("diagnostics") || []).length === 0) hide = true;
      if (hide) edge.addClass("filtered-out");
    });
    cy.nodes('[kind = "instance"]').removeClass("filtered-out");
    if (diagnosticsOnly) {
      cy.nodes('[kind = "instance"]').forEach(function (n) {
        const diags = (n.data("diagnostics") || []).concat(n.data("inheritedDiagnostics") || []);
        if (diags.length === 0) n.addClass("filtered-out");
      });
    }
  }

  // ── Overlays ─────────────────────────────────────────────────────────
  document.getElementById("overlay-select").addEventListener("change", function (evt) {
    applyOverlay(evt.target.value);
  });

  function applyOverlay(mode) {
    cy.nodes().forEach(function (n) { n.style({ "background-color": "", "background-opacity": "" }); });
    if (mode === "latency") {
      cy.nodes('[kind = "instance"]').forEach(function (n) {
        const lat = n.data("latency");
        if (lat && lat.cycles !== null && lat.cycles !== undefined) {
          const c = Math.min(lat.cycles, 20);
          n.style("background-color", "rgb(" + (240 - c * 6) + "," + (240 - c * 3) + ",240)");
        } else {
          n.style("background-color", "#f0f0f0");
        }
      });
    } else if (mode === "maturity") {
      cy.nodes('[kind = "instance"], [kind = "module-group"]').forEach(function (n) {
        const mat = n.data("maturity");
        if (mat) n.style("background-color", MATURITY_COLOR[mat.status] || "#eee");
      });
    } else if (mode === "verification") {
      cy.nodes('[kind = "module-group"]').forEach(function (n) {
        const obj = findObject("module-definition", n.data("module"));
        const flows = obj && obj.data && obj.data.verification_flow_entry_points || [];
        n.style("background-color", flows.length ? "#c8e6c9" : "#f5f5f5");
      });
    }
    renderLegend(mode);
  }

  function renderLegend(mode) {
    const el = document.getElementById("legend");
    el.innerHTML = "";
    let entries = [];
    if (mode === "maturity") {
      entries = Object.keys(MATURITY_COLOR).map(function (k) { return [k, MATURITY_COLOR[k]]; });
    } else if (mode === "none" || !mode) {
      entries = Object.keys(WIRING_COLOR).map(function (k) { return [k, WIRING_COLOR[k]]; });
    }
    entries.forEach(function (pair) {
      const row = document.createElement("div");
      row.innerHTML = '<span class="legend-swatch" style="background:' + pair[1] + '"></span>' + pair[0];
      el.appendChild(row);
    });
  }
  renderLegend("none");

  // ── Path-to/from-node (built-in Cytoscape.js core traversal) ────────
  let selectedForPath = null;
  document.getElementById("path-predecessors").addEventListener("click", function () {
    if (!selectedForPath) return;
    cy.elements().removeClass("path-highlight");
    selectedForPath.predecessors().addClass("path-highlight");
    selectedForPath.addClass("path-highlight");
  });
  document.getElementById("path-successors").addEventListener("click", function () {
    if (!selectedForPath) return;
    cy.elements().removeClass("path-highlight");
    selectedForPath.successors().addClass("path-highlight");
    selectedForPath.addClass("path-highlight");
  });
  document.getElementById("path-clear").addEventListener("click", function () {
    cy.elements().removeClass("path-highlight");
  });

  // ── Selected-object details panel (§8.4) ────────────────────────────
  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderDiagnostics(diags) {
    if (!diags || !diags.length) return "";
    return diags.map(function (d) {
      return '<div class="diag ' + escapeHtml(d.severity) + '">[' + escapeHtml(d.severity) + "] " + escapeHtml(d.message) + "</div>";
    }).join("");
  }

  function renderRow(label, value) {
    if (value === null || value === undefined || value === "") return "";
    return "<tr><td>" + escapeHtml(label) + "</td><td>" + escapeHtml(value) + "</td></tr>";
  }

  function renderInstanceDetails(n) {
    const module = n.data("module");
    const modObj = findObject("module-definition", module);
    let html = "<h2>Instance: " + escapeHtml(n.id()) + "</h2><table>";
    html += renderRow("module", module);
    html += renderRow("clock domain", n.data("clockDomain"));
    html += renderRow("reset domain", n.data("resetDomain"));
    const lat = n.data("latency");
    if (lat) {
      html += renderRow("latency (cycles)", lat.cycles);
      html += renderRow("latency kind", lat.kind);
      if (lat.provenance) html += renderRow("latency source", lat.provenance.source + (lat.provenance.detail ? " (" + lat.provenance.detail + ")" : ""));
    } else {
      html += renderRow("latency", "(no data)");
    }
    if (modObj) {
      html += renderRow("implementation kind", modObj.data.kind);
      html += renderRow("source files", (modObj.data.source_files || []).join(", "));
      html += renderRow("contract", modObj.data.contract_path);
    }
    html += "</table>";
    html += renderDiagnostics(n.data("diagnostics"));
    html += renderDiagnostics(n.data("inheritedDiagnostics"));
    if (modObj && modObj.data.interfaces && modObj.data.interfaces.length) {
      html += '<h2 style="font-size:.9em">Physical interfaces</h2>';
      modObj.data.interfaces.forEach(function (iface) {
        html += "<div><strong>" + escapeHtml(iface.name) + "</strong> (" + escapeHtml(iface.direction) + ")</div><table>";
        (iface.members || []).forEach(function (m) {
          const b = m.binding || {};
          const port = b.raw_port || b.raw_port_prefix || b.raw_port_tpl || "";
          html += renderRow(m.name, port + (b.width ? " (" + b.width + "b)" : ""));
        });
        html += "</table>";
      });
    } else if (modObj) {
      html += '<p class="empty-hint">No logical interfaces resolved for this module.</p>';
    }
    return html;
  }

  function renderModuleGroupDetails(n) {
    const modObj = findObject("module-definition", n.data("module"));
    let html = "<h2>Module: " + escapeHtml(n.data("module")) + "</h2><table>";
    if (modObj) {
      html += renderRow("kind", modObj.data.kind);
      html += renderRow("top", modObj.data.top);
      html += renderRow("instances", (modObj.data.instances || []).join(", "));
      html += renderRow("ports resolved", modObj.data.ports_resolved);
      if (modObj.data.maturity) html += renderRow("maturity", modObj.data.maturity.status);
      const flows = modObj.data.verification_flow_entry_points || [];
      html += renderRow("verification flow entry points", flows.join(", "));
    }
    html += "</table>";
    html += renderDiagnostics(n.data("diagnostics"));
    return html;
  }

  function renderConnectionDetails(e) {
    const obj = findObject("connection", e.id());
    let html = "<h2>Connection</h2><table>";
    html += renderRow("id", e.id());
    html += renderRow("wiring method", e.data("wiringMethod"));
    html += renderRow("crosses clock domain", e.data("crossesClock"));
    html += renderRow("crosses reset domain", e.data("crossesReset"));
    if (obj) {
      const ev = obj.data.matching_evidence;
      if (ev) {
        html += renderRow("producer protocol", ev.producer_protocol);
        html += renderRow("consumer protocol", ev.consumer_protocol);
        html += renderRow("producer width", ev.producer_width);
        html += renderRow("consumer width", ev.consumer_width);
        if (ev.rejected_candidates && ev.rejected_candidates.length) {
          html += renderRow("rejected candidates", ev.rejected_candidates.map(function (c) {
            return c.producer.instance_id + "." + (c.producer.port || "");
          }).join(", "));
        }
      }
    }
    const xforms = e.data("transformations") || [];
    if (xforms.length) {
      html += "</table><h2 style=\"font-size:.9em\">Transformations</h2><table>";
      xforms.forEach(function (x) {
        html += renderRow(x.kind, (x.cycles !== null && x.cycles !== undefined ? x.cycles + "c " : "") + (x.tag || ""));
      });
    }
    html += "</table>";
    html += renderDiagnostics(e.data("diagnostics"));
    return html;
  }

  function showDetails(html) {
    document.getElementById("details").innerHTML = html;
  }

  cy.on("tap", "node", function (evt) {
    const n = evt.target;
    selectedForPath = n;
    document.getElementById("path-predecessors").disabled = false;
    document.getElementById("path-successors").disabled = false;
    if (n.data("kind") === "instance") showDetails(renderInstanceDetails(n));
    else if (n.data("kind") === "module-group") showDetails(renderModuleGroupDetails(n));
    else if (n.data("kind") === "external-port") showDetails("<h2>External port: " + escapeHtml(n.data("label")) + "</h2>");
    else if (n.data("kind") === "domain-group") showDetails("<h2>" + escapeHtml(n.data("label")) + "</h2><p>" + (n.data("members") || []).join(", ") + "</p>");
  });
  cy.on("tap", "edge", function (evt) {
    const e = evt.target;
    if (e.hasClass("aggregate-edge")) {
      showDetails("<h2>Collapsed connections</h2><p>" + escapeHtml(e.data("label")) + " — expand the group to see individual connections.</p>");
      return;
    }
    selectedForPath = e;
    document.getElementById("path-predecessors").disabled = true;
    document.getElementById("path-successors").disabled = true;
    showDetails(renderConnectionDetails(e));
  });
  cy.on("tap", function (evt) {
    if (evt.target === cy) {
      selectedForPath = null;
      document.getElementById("path-predecessors").disabled = true;
      document.getElementById("path-successors").disabled = true;
    }
  });
})();
"""
