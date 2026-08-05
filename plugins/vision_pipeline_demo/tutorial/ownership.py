#!/usr/bin/env python3
"""Ownership vocabulary for vision_pipeline_demo's tutorial material.

Every path a tutorial reader is shown belongs to exactly one of five
categories: project-authored source, an artifact FORGE generates from
that source, an artifact a vendor toolchain (Vitis HLS/Vivado/XSim)
produces, an artifact produced by running the design and comparing
behavior, or tutorial/support metadata. ``classify()`` is the single
source of truth for that mapping — tutorial chapters
(docs/tutorials/vision-pipeline/**), the offline report bundle, and this
module's own tree renderers all defer to it rather than re-deriving
ownership by hand, so the two can never silently drift apart.

Textual labels are required in addition to any future color coding —
never communicate ownership by color alone.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

PROJECT_SOURCE = "PROJECT SOURCE"
FORGE_GENERATED = "FORGE GENERATED"
TOOLCHAIN_OUTPUT = "TOOLCHAIN OUTPUT"
VERIFICATION_RESULT = "VERIFICATION RESULT"
TUTORIAL_ASSET = "TUTORIAL ASSET"

CATEGORIES = (
    PROJECT_SOURCE,
    FORGE_GENERATED,
    TOOLCHAIN_OUTPUT,
    VERIFICATION_RESULT,
    TUTORIAL_ASSET,
)

LEGEND = {
    PROJECT_SOURCE: "Files a normal FORGE user owns and edits.",
    FORGE_GENERATED: "Artifacts generated directly by FORGE from project source.",
    TOOLCHAIN_OUTPUT: "Artifacts produced by Vitis HLS, Vivado, or XSim.",
    VERIFICATION_RESULT: "Artifacts produced by executing the design and comparing behavior.",
    TUTORIAL_ASSET: "Deterministic fixtures and metadata used only to teach the workflow.",
}

# Real per-flow directory names this plugin has today (forge/verify/<flow>/).
# Kept as a real, verified list rather than a loose wildcard so a renamed or
# added flow is a deliberate one-line update here, not a silent match.
_FLOW_DIRS = (
    "pixel_normalizer_csim",
    "quickstart_pipeline_xsim",
    "pixel_result_xsim",
    "tile_stats_xsim",
    "packetizer_xsim",
    "invalid_fifo_depth_xsim",
    "full_functional_xsim",
    "platform_wrapper_xsim",
    "cdc_xsim",
)
_FLOW_DIR_ALT = "(?:" + "|".join(re.escape(f) for f in _FLOW_DIRS) + ")"

# Ordered (pattern, category) rules, most specific first. Every pattern is
# matched against a POSIX path relative to the repository root.
_RULES = (
    # Toolchain output: real Vitis HLS / Vivado xsim work areas.
    (re.compile(r"^build_hls_vision_pipeline_demo/"), TOOLCHAIN_OUTPUT),
    (re.compile(rf"^plugins/vision_pipeline_demo/forge/verify/{_FLOW_DIR_ALT}/xsim_work/"), TOOLCHAIN_OUTPUT),

    # Verification/runtime output: produced by running a flow and comparing
    # observed behavior against a golden model, not by FORGE's generators.
    (re.compile(rf"^plugins/vision_pipeline_demo/forge/verify/{_FLOW_DIR_ALT}/(throughput_result\.json|golden_model_provenance\.json|.*_probe\.csv)$"), VERIFICATION_RESULT),

    # FORGE-generated: written by `forge topgen gen-top` / `forge verify
    # generate` / `gen_stimulus_*.py`'s emitter, all carrying their own
    # "DO NOT EDIT — regenerate with ..." header.
    (re.compile(r"^gen-top/design_vision_pipeline_[a-z_]+/"), FORGE_GENERATED),
    (re.compile(rf"^plugins/vision_pipeline_demo/forge/verify/{_FLOW_DIR_ALT}/(verify\.flow\.yml|tb_algo_top\.sv|port_map\.yaml|wave\.tcl|stimulus_current\.svh)$"), FORGE_GENERATED),

    # Tutorial/support material: read by a human learning the workflow,
    # not edited to change the design and not produced by running it.
    (re.compile(r"^plugins/vision_pipeline_demo/README\.md$"), TUTORIAL_ASSET),
    (re.compile(r"^plugins/vision_pipeline_demo/tutorial\.yml$"), TUTORIAL_ASSET),
    (re.compile(r"^plugins/vision_pipeline_demo/tutorial/"), TUTORIAL_ASSET),

    # Everything else under the plugin tree that isn't matched above is
    # project-authored: algo/, datasets/, forge/modules.yml,
    # forge/designs/**, forge/interfaces/**, forge/verify/design.verification.yml,
    # forge/verify/{include,src,tests,tools,schemas}/**, and each flow
    # dir's own CMakeLists.txt.
    (re.compile(r"^plugins/vision_pipeline_demo/"), PROJECT_SOURCE),
)


def classify(rel_path: str) -> str:
    """Return one of :data:`CATEGORIES` for *rel_path* (POSIX, repo-root-relative).

    Raises ``ValueError`` for a path outside this plugin's own tree and the
    generated roots it writes to — callers should only classify paths they
    already know belong to vision_pipeline_demo's tutorial material.
    """
    posix = PurePosixPath(rel_path).as_posix()
    for pattern, category in _RULES:
        if pattern.match(posix):
            return category
    raise ValueError(f"no ownership rule matches {rel_path!r}")


def render_source_tree() -> str:
    """A hand-curated, ownership-annotated view of the real
    ``plugins/vision_pipeline_demo/`` source tree, cross-checked against
    :func:`classify` by this module's own tests rather than left to drift.
    """
    entries = [
        ("algo/", "algo/normalizer/pixel_normalizer.cpp"),
        ("datasets/", "datasets/model.py"),
        ("forge/modules.yml", "forge/modules.yml"),
        ("forge/designs/", "forge/designs/design.yml"),
        ("forge/interfaces/", "forge/interfaces/pixel_normalizer_ip.interface.yaml"),
        ("forge/verify/design.verification.yml", "forge/verify/design.verification.yml"),
        ("forge/verify/{include,src,tests,tools,schemas}/", "forge/verify/tools/golden_model_provider.py"),
        ("tutorial.yml", "tutorial.yml"),
        ("tutorial/", "tutorial/ownership.py"),
        ("README.md", "README.md"),
    ]
    lines = ["plugins/vision_pipeline_demo/"]
    width = max(len(label) for label, _ in entries)
    for i, (label, probe) in enumerate(entries):
        branch = "└──" if i == len(entries) - 1 else "├──"
        category = classify(f"plugins/vision_pipeline_demo/{probe}")
        lines.append(f"{branch} {label.ljust(width)}  [{category}]")
    return "\n".join(lines)


def render_generated_roots_tree() -> str:
    """Ownership-annotated view of where this plugin's generated output
    actually lands. There is no single unified build root today —
    generated output is split across three real locations (the RTL
    generator's own output tree, the HLS toolchain's own build root, and
    each verify flow's own directory), reflected here as-is rather than
    papered over.
    """
    lines = [
        "gen-top/design_vision_pipeline_<design>/",
        f"└── algo_top.v" + " " * 10 + f"[{FORGE_GENERATED}]",
        "",
        "build_hls_vision_pipeline_demo/<module>/",
        f"└── solution1/, logs/" + " " * 2 + f"[{TOOLCHAIN_OUTPUT}]",
        "",
        "plugins/vision_pipeline_demo/forge/verify/<flow>/",
        f"├── verify.flow.yml, tb_algo_top.sv,   [{FORGE_GENERATED}]",
        f"│   port_map.yaml, wave.tcl,",
        f"│   stimulus_current.svh",
        f"├── xsim_work/                         [{TOOLCHAIN_OUTPUT}]",
        f"└── throughput_result.json,            [{VERIFICATION_RESULT}]",
        f"    golden_model_provenance.json",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_source_tree())
    print()
    print(render_generated_roots_tree())
