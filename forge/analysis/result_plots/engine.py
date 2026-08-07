"""forge.analysis.result_plots.engine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Generic plotting engine.  The plugin supplies ``plot_config.yml`` with column
names and plot kinds; the framework renders the figures.

No OMTF or detector-specific semantics belong here.  Column names, axis
labels, and physical interpretation are entirely plugin-defined through the
config file.

matplotlib is an optional dependency.  When it is absent the engine prints a
warning for each skipped plot and continues; a CSV summary is always written.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, List, Optional

from forge.analysis.result_plots.config import PlotSpec


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _to_float(values: List[str]) -> List[float]:
    result: List[float] = []
    for v in values:
        try:
            result.append(float(v))
        except (ValueError, TypeError):
            result.append(float("nan"))
    return result


# ---------------------------------------------------------------------------
# matplotlib helpers
# ---------------------------------------------------------------------------

def _try_import_matplotlib():
    """Return (matplotlib, pyplot) or (None, None) if unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend — safe in headless CI
        import matplotlib.pyplot as plt
        return matplotlib, plt
    except ImportError:
        return None, None


def _render_figure(
    plt,
    spec: PlotSpec,
    observed: List[Dict],
    reference: Optional[List[Dict]],
    out_path: Path,
) -> None:
    """Render a single figure and write it to *out_path*."""
    fig, ax = plt.subplots(figsize=spec.figsize)

    x_obs = _to_float([r.get(spec.x, "nan") for r in observed])
    y_obs = _to_float([r.get(spec.y, "nan") for r in observed])

    if spec.kind == "scatter":
        ax.scatter(x_obs, y_obs, label="observed", s=20, alpha=0.7)
        if reference:
            x_ref = _to_float([r.get(spec.x, "nan") for r in reference])
            y_ref = _to_float([r.get(spec.y, "nan") for r in reference])
            ax.scatter(x_ref, y_ref, label="reference", s=20, alpha=0.7, marker="x")

    elif spec.kind == "bar":
        labels = [r.get(spec.x, "") for r in observed]
        ax.bar(range(len(labels)), y_obs, tick_label=labels)
        plt.xticks(rotation=45, ha="right")

    elif spec.kind == "histogram":
        ax.hist([v for v in y_obs if v == v], bins=30, label="observed", alpha=0.7)
        if reference:
            y_ref = _to_float([r.get(spec.y, "nan") for r in reference])
            ax.hist([v for v in y_ref if v == v], bins=30, label="reference", alpha=0.7)

    elif spec.kind == "line":
        ax.plot(x_obs, y_obs, label="observed")
        if reference:
            x_ref = _to_float([r.get(spec.x, "nan") for r in reference])
            y_ref = _to_float([r.get(spec.y, "nan") for r in reference])
            ax.plot(x_ref, y_ref, label="reference", linestyle="--")

    ax.set_xlabel(spec.xlabel or spec.x)
    ax.set_ylabel(spec.ylabel or spec.y)
    ax.set_title(spec.title or spec.name)
    ax.grid(True, alpha=0.3)

    if spec.kind in ("scatter", "histogram", "line") and reference:
        ax.legend()

    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_plots(
    specs: List[PlotSpec],
    observed_csv: Path,
    output_dir: Path,
    reference_csv: Optional[Path] = None,
) -> List[Path]:
    """Generate PNG figures for each :class:`PlotSpec` in *specs*.

    Parameters
    ----------
    specs:
        List of plot specifications loaded from ``plot_config.yml``.
    observed_csv:
        CSV file with observed / simulated values.
    output_dir:
        Directory where ``.png`` files are written.  Created if absent.
    reference_csv:
        Optional reference / expected CSV file (e.g. emulator output).

    Returns
    -------
    List of paths of successfully written PNG files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    observed  = _load_csv(observed_csv)
    reference = _load_csv(reference_csv) if reference_csv else None

    _mpl, plt = _try_import_matplotlib()
    written: List[Path] = []

    for spec in specs:
        out_path = output_dir / f"{spec.name}.png"
        if plt is None:
            print(
                f"  [skip] {spec.name}: matplotlib not installed "
                "(pip install matplotlib)",
                file=sys.stderr,
            )
            continue
        try:
            _render_figure(plt, spec, observed, reference, out_path)
            written.append(out_path)
            print(f"  [plot] {out_path}")
        except Exception as exc:
            print(f"  [FAIL] {spec.name}: {exc}", file=sys.stderr)

    return written
