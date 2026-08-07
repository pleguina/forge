#!/usr/bin/env python3
"""Generate (or --check the staleness of) this plugin's small, committed
tutorial reference figures under docs/assets/generated/vision-pipeline/.

Model (heavy Vivado/Vitis runs must not be required merely to build
MkDocs — a normal `mkdocs build --strict` only ever reads the committed
figures this script produces):

1. Architecture figures (`diagrams/*.svg`) are rendered by FORGE core's
   own generic `forge inspect --svg` from each real design — no
   vision-domain code involved.
2. Functional image figures (`figures/*-{input,normalized,gradient,
   mask,tile-overlay}.png`) are rendered by this plugin's own
   `tutorial/visualizations/image_panels.py`, from a real golden dataset
   XML plus this plugin's own `GoldenModelProvider` output — never
   invented pixel values.
3. Performance figures (`figures/fifo-high-water-mark.png`) are rendered
   by FORGE core's generic `forge analyze plot-results`, fed a CSV built
   from this plugin's own checked-in `throughput_result.json` files.
4. `manifest.json` records the design content hash (for diagrams) or
   source dataset hash (for image/performance figures) each committed
   figure was generated from, so `--check` can prove they're still
   fresh without re-running Vivado/Vitis.

Usage:
    python3 plugins/vision_pipeline_demo/tutorial/generate_reference_assets.py
    python3 plugins/vision_pipeline_demo/tutorial/generate_reference_assets.py --check
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util as _ilu
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PLUGIN_ROOT.parents[1]
_DESIGNS_DIR = _PLUGIN_ROOT / "forge" / "designs"
_MODULES_YML = _PLUGIN_ROOT / "forge" / "modules.yml"
_DATA_DIR = _PLUGIN_ROOT / "forge" / "verify" / "schemas" / "data"
_OUTPUT_ROOT = _REPO_ROOT / "docs" / "assets" / "generated" / "vision-pipeline"

DIAGRAM_DESIGNS = [
    ("quickstart", "design.yml"),
    ("pixel-result", "design_pixel_result.yml"),
    ("tile-stats", "design_tile_stats.yml"),
    ("cdc", "design_cdc.yml"),
    ("full-functional", "design_full_functional.yml"),
    ("platform-wrapper", "design_platform_wrapper.yml"),
]


def _load_image_panels():
    viz_dir = _PLUGIN_ROOT / "tutorial" / "visualizations"
    spec = _ilu.spec_from_file_location("vpd_image_panels_gen", viz_dir / "image_panels.py")
    module = _ilu.module_from_spec(spec)  # type: ignore[union-attr]
    spec.loader.exec_module(module)       # type: ignore[union-attr]
    return module


def _forge_inspect_json(design_path: Path) -> dict:
    result = subprocess.run(
        ["forge", "inspect", str(design_path), "--contracts-from", str(_MODULES_YML), "--json"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _forge_inspect_svg(design_path: Path, output_path: Path) -> None:
    subprocess.run(
        ["forge", "inspect", str(design_path), "--contracts-from", str(_MODULES_YML), "--svg", str(output_path)],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    )


def generate_diagrams(output_dir: Path, manifest: dict) -> None:
    diagrams_dir = output_dir / "diagrams"
    for name, design_file in DIAGRAM_DESIGNS:
        design_path = _DESIGNS_DIR / design_file
        info = _forge_inspect_json(design_path)
        svg_path = diagrams_dir / f"{name}.svg"
        _forge_inspect_svg(design_path, svg_path)
        manifest["diagrams"][name] = {
            "source_design": f"forge/designs/{design_file}",
            "design_content_hash": info["metrics"]["content_hash"],
        }


def _dataset_hash(xml_path: Path) -> str:
    return hashlib.sha256(xml_path.read_bytes()).hexdigest()


def generate_image_figures(output_dir: Path, manifest: dict) -> None:
    ip = _load_image_panels()
    figures_dir = output_dir / "figures"

    # quickstart: input / normalized / threshold mask (no gradient -- no
    # Sobel module in this design).
    xml_path = _DATA_DIR / "vision_pipeline_quickstart_golden.xml"
    ds = ip.load_dataset(xml_path)
    gmp = ip._load_golden_model_provider_module()
    result = gmp.QuickstartNormalizerThresholdProvider().evaluate(ds, {})
    events = [{"in": ev["in"], "expected": exp["expected"]} for ev, exp in zip(ds.events, result.events)]
    ip.render_grayscale_panel(ip.grid_from_events(ds.events, 8, 8, "pixel", source="in"), figures_dir / "quickstart-input.png")
    ip.render_grayscale_panel(ip.grid_from_events(events, 8, 8, "normalized_pixel", source="expected"), figures_dir / "quickstart-normalized.png")
    ip.render_grayscale_panel(ip.grid_from_events(events, 8, 8, "threshold_mask", source="expected"), figures_dir / "quickstart-mask.png", value_max=1)
    manifest["figures"]["quickstart-input"] = {"source_dataset": "quickstart_golden.xml", "dataset_hash": _dataset_hash(xml_path)}
    manifest["figures"]["quickstart-normalized"] = manifest["figures"]["quickstart-input"]
    manifest["figures"]["quickstart-mask"] = manifest["figures"]["quickstart-input"]

    # pixel-result: input / normalized / gradient magnitude / threshold mask.
    result = gmp.PixelResultProvider().evaluate(ds, {})
    events = [{"in": ev["in"], "expected": exp["expected"]} for ev, exp in zip(ds.events, result.events)]
    gradient_grid = ip.grid_from_events(events, 8, 8, "gradient_magnitude", source="expected")
    gradient_max = max(1, max(max(row) for row in gradient_grid))
    ip.render_grayscale_panel(ip.grid_from_events(ds.events, 8, 8, "pixel", source="in"), figures_dir / "pixel-result-input.png")
    ip.render_grayscale_panel(ip.grid_from_events(events, 8, 8, "normalized_pixel", source="expected"), figures_dir / "pixel-result-normalized.png")
    ip.render_grayscale_panel(gradient_grid, figures_dir / "pixel-result-gradient.png", value_max=gradient_max)
    ip.render_grayscale_panel(ip.grid_from_events(events, 8, 8, "threshold_mask", source="expected"), figures_dir / "pixel-result-mask.png", value_max=1)
    for key in ("pixel-result-input", "pixel-result-normalized", "pixel-result-gradient", "pixel-result-mask"):
        manifest["figures"][key] = {"source_dataset": "quickstart_golden.xml", "dataset_hash": _dataset_hash(xml_path)}

    # tile-stats (quickstart scale, single tile) tile overlay.
    result = gmp.TileStatsProvider().evaluate(ds, {})
    tile_records = [exp["expected"] for exp in result.events]
    ip.render_tile_overlay(
        ip.grid_from_events(ds.events, 8, 8, "pixel", source="in"),
        tile_records, tile_width=8, tile_height=8,
        output_path=figures_dir / "tile-stats-overlay.png",
    )
    manifest["figures"]["tile-stats-overlay"] = {"source_dataset": "quickstart_golden.xml", "dataset_hash": _dataset_hash(xml_path)}

    # full-functional (16x16, real 2x2 tile grid) tile overlay.
    ff_xml_path = _DATA_DIR / "vision_pipeline_full_functional_golden.xml"
    ff_ds = ip.load_dataset(ff_xml_path)
    ff_result = gmp.TileStatsProvider().evaluate(ff_ds, {})
    ff_tile_records = [exp["expected"] for exp in ff_result.events]
    ip.render_tile_overlay(
        ip.grid_from_events(ff_ds.events, 16, 16, "pixel", source="in"),
        ff_tile_records, tile_width=8, tile_height=8,
        output_path=figures_dir / "full-functional-tile-overlay.png",
    )
    manifest["figures"]["full-functional-tile-overlay"] = {"source_dataset": "full_functional_golden.xml", "dataset_hash": _dataset_hash(ff_xml_path)}


def generate_performance_figures(output_dir: Path, manifest: dict) -> None:
    figures_dir = output_dir / "figures"
    rows = []
    sources = []
    for flow, short in [("packetizer_xsim", "packetizer 8x8"), ("full_functional_xsim", "full_functional 16x16")]:
        result_path = _PLUGIN_ROOT / "forge" / "verify" / flow / "throughput_result.json"
        with open(result_path) as f:
            data = json.load(f)
        sources.append(str(result_path.relative_to(_REPO_ROOT)))
        for r in data["runtime"]:
            kind = "pixel-result" if "pr_record" in r["fifo_object_id"] else "tile-statistics"
            rows.append({"crossing": f"{short}\n{kind}", "high_water_mark": r["high_water_mark"]})

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "fifo_occupancy.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["crossing", "high_water_mark"])
            writer.writeheader()
            writer.writerows(rows)

        config_path = Path(tmp) / "plot_config.yml"
        config_path.write_text(
            "plots:\n"
            "  - name: fifo-high-water-mark\n"
            "    kind: bar\n"
            "    x: crossing\n"
            "    y: high_water_mark\n"
            '    title: "Async FIFO High-Water Mark (real, measured)"\n'
            '    xlabel: "Flow / crossing"\n'
            '    ylabel: "High-water mark (records)"\n'
            "    figsize: [8, 5]\n"
        )
        subprocess.run(
            ["forge", "analyze", "plot-results", "--config", str(config_path),
             "--observed", str(csv_path), "--output", str(figures_dir)],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
        )

    source_hash = hashlib.sha256("".join(sources).encode() + json.dumps(rows, sort_keys=True).encode()).hexdigest()
    manifest["figures"]["fifo-high-water-mark"] = {"source_dataset": sources, "dataset_hash": source_hash}


def generate_all(output_dir: Path) -> dict:
    manifest = {"schema_version": 1, "diagrams": {}, "figures": {}}
    (output_dir / "diagrams").mkdir(parents=True, exist_ok=True)
    (output_dir / "figures").mkdir(parents=True, exist_ok=True)
    generate_diagrams(output_dir, manifest)
    generate_image_figures(output_dir, manifest)
    generate_performance_figures(output_dir, manifest)
    return manifest


def _load_manifest(output_dir: Path) -> dict:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"schema_version": 1, "diagrams": {}, "figures": {}}
    with open(manifest_path) as f:
        return json.load(f)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify committed assets are fresh; write nothing.")
    args = parser.parse_args(argv)

    if args.check:
        committed_manifest = _load_manifest(_OUTPUT_ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fresh_manifest = generate_all(tmp_path)

            mismatches = []
            if fresh_manifest["diagrams"] != committed_manifest.get("diagrams"):
                mismatches.append("diagrams manifest (design content hash changed)")
            if fresh_manifest["figures"] != committed_manifest.get("figures"):
                mismatches.append("figures manifest (source dataset hash changed)")

            # Byte-for-byte, so a renderer-code change with unchanged
            # source data is caught too, not just a source-data change.
            for rel in sorted(list((tmp_path / "diagrams").glob("*")) + list((tmp_path / "figures").glob("*"))):
                rel_path = rel.relative_to(tmp_path)
                committed_path = _OUTPUT_ROOT / rel_path
                if not committed_path.is_file() or committed_path.read_bytes() != rel.read_bytes():
                    mismatches.append(f"file differs: {rel_path}")

        if mismatches:
            print("STALE: committed reference assets do not match a fresh regeneration:", file=sys.stderr)
            for m in mismatches:
                print(f"  - {m}", file=sys.stderr)
            print("Run this script without --check to regenerate, then commit the result.", file=sys.stderr)
            return 1
        print("PASS: all reference assets are fresh.")
        return 0

    manifest = generate_all(_OUTPUT_ROOT)
    with open(_OUTPUT_ROOT / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Generated {len(manifest['diagrams'])} diagram(s) and {len(manifest['figures'])} figure(s) under {_OUTPUT_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
