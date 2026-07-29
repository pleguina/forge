"""
Tests for design.yml-level parsing/validation of the release-plan §3.2
schema additions: `Connection.cdc` and the top-level `clock_domains:`/
`reset_domains:` relationship-declaration blocks
(`forge/topgen/config.py::DesignConfig.load`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.topgen.config import DesignConfig

_BASE = """\
part: xcvu13p
clock_period: 4.0
modules:
  - name: src
    top: src_top
    src: [src.v]
  - name: dst
    top: dst_top
    src: [dst.v]
"""


def _write(tmp_path: Path, extra: str) -> Path:
    p = tmp_path / "design.yml"
    p.write_text(_BASE + extra)
    return p


class TestCdcConnectionField:
    def test_valid_2ff_sync_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: 2ff_sync}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.connections[0].cdc == {"kind": "2ff_sync"}

    def test_valid_async_fifo_with_depth_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: async_fifo, depth: 4}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.connections[0].cdc == {"kind": "async_fifo", "depth": 4}

    def test_no_cdc_declared_stays_none(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.connections[0].cdc is None

    def test_unknown_kind_raises(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: made_up}\n"
        ))
        with pytest.raises(ValueError, match="kind"):
            DesignConfig.load_relaxed(design)

    def test_missing_kind_raises(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {depth: 4}\n"
        ))
        with pytest.raises(ValueError, match="kind"):
            DesignConfig.load_relaxed(design)

    def test_negative_depth_raises(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: async_fifo, depth: -1}\n"
        ))
        with pytest.raises(ValueError, match="depth"):
            DesignConfig.load_relaxed(design)


class TestDomainRelationshipBlocks:
    def test_valid_clock_domains_block_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "clock_domains:\n"
            "  ap_clk: {}\n"
            "  clk_slow: {derived_from: ap_clk, ratio: 4}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.clock_domains["ap_clk"] == {"derived_from": None, "ratio": None}
        assert cfg.clock_domains["clk_slow"] == {"derived_from": "ap_clk", "ratio": 4}

    def test_no_block_declared_stays_empty(self, tmp_path):
        design = _write(tmp_path, "")
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.clock_domains == {}
        assert cfg.reset_domains == {}

    def test_reset_domains_block_parses_independently(self, tmp_path):
        design = _write(tmp_path, "reset_domains:\n  ap_rst: {}\n")
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.reset_domains == {"ap_rst": {"derived_from": None, "ratio": None}}
        assert cfg.clock_domains == {}

    def test_non_mapping_block_raises(self, tmp_path):
        design = _write(tmp_path, "clock_domains: [not, a, mapping]\n")
        with pytest.raises(ValueError, match="clock_domains"):
            DesignConfig.load_relaxed(design)

    def test_invalid_ratio_raises(self, tmp_path):
        design = _write(tmp_path, "clock_domains:\n  clk_slow: {ratio: -1}\n")
        with pytest.raises(ValueError, match="ratio"):
            DesignConfig.load_relaxed(design)
