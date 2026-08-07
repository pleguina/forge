"""forge.analysis.result_plots.config
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Parse a ``plot_config.yml`` file into a list of :class:`PlotSpec` objects.

Schema
------
::

    plots:
      - name: phi_residual
        kind: scatter          # scatter | bar | histogram | line
        x: event_id
        y: rtl_phi_minus_emu_phi
        title: "φ Residual (RTL − EMU)"   # optional
        xlabel: "Event ID"                  # optional
        ylabel: "Δφ"                        # optional
        hue:   layer                        # optional column for colour-coding
        figsize: [12, 5]                    # optional, default [10, 6]

      - name: mismatch_by_layer
        kind: bar
        x: layer
        y: mismatch_count

Columns ``x``, ``y`` (and ``hue`` when given) must be present in the CSV
files passed to the plotting engine.  The framework does not validate column
existence at config-load time; validation happens at render time.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import List, Optional, Tuple

import yaml


@dataclasses.dataclass
class PlotSpec:
    name: str
    kind: str                   # scatter | bar | histogram | line
    x: str
    y: str
    title: Optional[str] = None
    xlabel: Optional[str] = None
    ylabel: Optional[str] = None
    hue: Optional[str] = None   # column used for colour-coding
    figsize: Tuple[int, int] = (10, 6)


def load_plot_config(path: Path) -> List[PlotSpec]:
    """Load *path* and return a list of :class:`PlotSpec`."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    specs: List[PlotSpec] = []
    for entry in raw.get("plots", []):
        specs.append(PlotSpec(
            name=entry["name"],
            kind=entry.get("kind", "scatter"),
            x=entry["x"],
            y=entry["y"],
            title=entry.get("title"),
            xlabel=entry.get("xlabel"),
            ylabel=entry.get("ylabel"),
            hue=entry.get("hue"),
            figsize=tuple(entry.get("figsize", [10, 6])),
        ))
    return specs
