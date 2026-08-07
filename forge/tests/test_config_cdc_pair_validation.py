"""Tests for DesignConfig.load's ATG027 check:
forge.generation.generators.structural_verilog's cdc_map is
keyed by (src_module, dst_module) alone, not per-pin — declaring two
`cdc:` connections between the same module pair with *different* kinds
used to merge silently (the second declaration's kind was applied to
every pin between that pair, with zero diagnostic). This is now a real,
actionable load-time error instead.

Found wiring plugins/vision_pipeline_demo/forge/designs/design_cdc.yml —
the first design in this repo to attempt more than one
distinct cdc kind between the same two module instances.
"""
from __future__ import annotations

import textwrap

import pytest

from forge.contracts.config import DesignConfig


_YAML_HEADER = """\
schema_version: "1.0"
part: xcvu13p
clock_period: 4.0
block_protocol: none

modules:
  - name: src
    top: src_top
    src: []
  - name: dst
    top: dst_top
    src: []

connections:
"""


def _write(tmp_path, connections_yaml: str):
    path = tmp_path / "design.yml"
    path.write_text(_YAML_HEADER + textwrap.indent(connections_yaml, "  "))
    return path


class TestConflictingCdcKindsBetweenSamePair:
    def test_two_different_kinds_between_same_pair_raises_atg027(self, tmp_path):
        path = _write(tmp_path, textwrap.dedent("""\
            - from: src
              to: dst
              port_map: [[level_out, level_in]]
              cdc: {kind: level_sync}
            - from: src
              to: dst
              port_map: [[pulse_out, pulse_in]]
              cdc: {kind: pulse_sync, min_spacing_cycles: 8}
        """))
        with pytest.raises(ValueError, match=r"\[ATG027\]"):
            DesignConfig.load(path, validate_sources=False)

    def test_same_kind_twice_between_same_pair_is_allowed(self, tmp_path):
        """Two connections between the same pair declaring the *same* cdc
        kind don't hit the silent-overwrite hazard (both would resolve to
        the intended kind either way) — only a genuine kind mismatch is an
        error."""
        path = _write(tmp_path, textwrap.dedent("""\
            - from: src
              to: dst
              port_map: [[a_out, a_in]]
              cdc: {kind: level_sync}
            - from: src
              to: dst
              port_map: [[b_out, b_in]]
              cdc: {kind: level_sync}
        """))
        cfg = DesignConfig.load(path, validate_sources=False)
        assert len(cfg.connections) == 2

    def test_different_kinds_on_different_pairs_is_allowed(self, tmp_path):
        """The collision is specific to the (src, dst) module pair — a
        second module pair using a different kind is unaffected."""
        path = tmp_path / "design3.yml"
        path.write_text(textwrap.dedent("""\
            schema_version: "1.0"
            part: xcvu13p
            clock_period: 4.0
            block_protocol: none

            modules:
              - name: src
                top: src_top
                src: []
              - name: dst
                top: dst_top
                src: []
              - name: dst2
                top: dst2_top
                src: []

            connections:
              - from: src
                to: dst
                port_map: [[level_out, level_in]]
                cdc: {kind: level_sync}
              - from: src
                to: dst2
                port_map: [[pulse_out, pulse_in]]
                cdc: {kind: pulse_sync, min_spacing_cycles: 8}
        """))
        cfg = DesignConfig.load(path, validate_sources=False)
        assert len(cfg.connections) == 2
