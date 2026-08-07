# Vendored: Cytoscape.js (core)

The self-contained interactive HTML design explorer's client-side graph
rendering/pan/zoom/layout engine.

- **Package**: `cytoscape` (npm), **core build only** — no plugins/
  extensions vendored (see "Why core-only" below).
- **Pinned version**: `3.28.1`
- **Upstream source**: `https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js`
  (npm-backed CDN mirror of the published `cytoscape@3.28.1` package;
  upstream project: `https://js.cytoscape.org`,
  `https://github.com/cytoscape/cytoscape.js`)
- **License**: MIT — `LICENSE` in this directory is the verbatim upstream
  `LICENSE` file from the same npm package/version.
- **`cytoscape.min.js` sha256**: `92d752b48ea949720675865197fd2a0001c95bc5888545e990af60321712d4c6`
- **Source-map comments**: none present in the upstream minified file as
  published (no `//# sourceMappingURL=...` line) — nothing was stripped;
  this is the file exactly as published.

## Why core-only (not the expand-collapse extension)

Cytoscape.js core's compound-node feature is sufficient for this design
explorer's module-definition/clock-domain grouping. Collapse/expand
interactivity is implemented as small, local JavaScript in the explorer's own
inline script instead of vendoring the official
`cytoscape.js-expand-collapse` extension — the reference designs are
small (≤45 edges), and a second vendored JS dependency (its own license/
version/hash bookkeeping) is not justified by that scale. Documented as
the upgrade path if a future, larger reference design needs richer
collapse behavior.

## How it's loaded

Read via `importlib.resources` (never a `<script src=...>` — the
explorer must load fully offline) and inlined as one `<script>` block by
`forge/analyze/design_explorer/html_renderer.py`.

## Verifying the pin

```
sha256sum cytoscape.min.js
# 92d752b48ea949720675865197fd2a0001c95bc5888545e990af60321712d4c6
```
