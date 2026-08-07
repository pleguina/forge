"""
Real-design agreement test between the two independent latency-summing
codepaths:

- forge.generation.generators.design_parameters.extract_design_parameters's
  naive, DAG-blind cumulative-latency walker (walks cfg.modules in
  design.yml file order, sums worst_case_latency — no notion that two
  modules might feed the same consumer in parallel rather than
  sequentially).
- forge.analysis.latency_static (LatencyGraph/check_merge_points), the
  DAG-aware alternative.

This does NOT prove the two agree everywhere — they don't, and can't:
trigger_demo's real dec x4 -> col/partmon fan-out is exactly the case the
naive walker gets wrong (it just adds every module's latency once, in
file order, with no concept of parallelism). It proves they agree on
trigger_demo's real *linear* tail (col -> trig -> tfan -> tsink -> tout,
a genuine 1-in-1-out chain per design.yml's connections:), which is the
honest, narrower claim documented in design_parameters.py's own
cross-reference comment.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge.analysis.latency_static.graph import build_graph
from forge.contracts.config import DesignConfig
from forge.generation.generators.design_parameters import extract_design_parameters

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"

# The real linear tail in trigger_demo/forge/designs/design.yml's
# connections: col->trig->tfan->tsink->tout (each a plain 1:1 Connection,
# confirmed by reading the file — not the dec x4 fan-in region).
_LINEAR_CHAIN = ["col", "trig", "tfan", "tsink", "tout"]


def _synthetic_hls_metrics_matching_real_latency_hints(cfg: DesignConfig) -> dict:
    """Build an hls_metrics.json payload using each real module's own
    ModuleTiming-resolved cycle count as worst_case_latency — so both
    codepaths are seeded with the exact same per-module latency facts,
    making a genuine (not tautological-by-mismatched-input) agreement
    comparison possible."""
    modules = {}
    for mod in cfg.modules:
        timing = mod.timing
        if timing and timing.latency is not None and timing.latency.cycles is not None:
            cycles = timing.latency.cycles
        elif timing and timing.latency_cycles is not None:
            cycles = timing.latency_cycles
        elif timing and timing.latency_hint is not None:
            cycles = timing.latency_hint
        else:
            cycles = 0
        modules[mod.name] = {
            "status": "success",
            "latency": {
                "best_case_latency": cycles,
                "average_case_latency": cycles,
                "worst_case_latency": cycles,
                "pipeline_ii": 1,
                "pipeline_depth": 0,
                "pipeline_type": "no",
            },
            "timing": {"timing_met": True},
        }
    return {"modules": modules}


def test_linear_chain_cumulative_latency_agrees_with_dag_aware_graph(tmp_path):
    cfg = DesignConfig.load_relaxed(TRIGGER_DESIGN)

    hls_metrics_file = tmp_path / "hls_metrics.json"
    hls_metrics_file.write_text(json.dumps(_synthetic_hls_metrics_matching_real_latency_hints(cfg)))

    params = extract_design_parameters(cfg, hls_metrics_file=hls_metrics_file)
    # extract_design_parameters nests HLS-derived pipeline data under the
    # 'pipeline' key (confirmed by reading the function's own return dict).
    naive_modules = params["pipeline"]["modules"]

    graph = build_graph(TRIGGER_DESIGN)

    # Walk the real linear chain and independently accumulate module-only
    # latency (no edge/register_stages contribution — the naive walker
    # never included that either, so this is an apples-to-apples
    # comparison of exactly what design_parameters.py itself computes).
    dag_cumulative = 0
    for name in _LINEAR_CHAIN:
        naive_entry = naive_modules[name]
        assert naive_entry["cumulative_latency"] == dag_cumulative, (
            f"module '{name}': naive walker's cumulative_latency "
            f"({naive_entry['cumulative_latency']}) diverged from the "
            f"DAG-aware running total ({dag_cumulative}) on the real "
            "linear chain col->trig->tfan->tsink->tout"
        )
        dag_cumulative += graph.nodes[name].latency_cycles or 0

    # Sanity: the chain isn't trivially all-zero for every module — 'trig'
    # really does carry the real, non-zero latency: {kind: fixed, cycles:
    # 3} declaration, so this
    # test is exercising real arithmetic, not just comparing zeros.
    assert graph.nodes["trig"].latency_cycles == 3
    assert naive_modules["trig"]["latency_max"] == 3


def test_naive_walker_output_shape_documented() -> None:
    """Guards the exact return-dict shape this test (and the cross-
    reference comment in design_parameters.py) depends on, so a future
    refactor of extract_design_parameters's return shape fails loudly
    here instead of silently invalidating the agreement proof above."""
    cfg = DesignConfig.load_relaxed(TRIGGER_DESIGN)
    params = extract_design_parameters(cfg, hls_metrics_file=None)
    assert "pipeline" in params
    assert "modules" in params["pipeline"]
    assert "total_latency_cycles" in params["pipeline"]
