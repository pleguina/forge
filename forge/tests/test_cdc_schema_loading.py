"""
Tests for design.yml-level parsing/validation of the schema
additions: `Connection.cdc` and the top-level `clock_domains:`/
`reset_domains:` relationship-declaration blocks
(`forge/topgen/config.py::DesignConfig.load`), plus the CDC primitive
family expansion (`level_sync`/`2ff_sync` alias, `pulse_sync`,
`mailbox_transfer`, `async_fifo`'s now-required `depth`, and
`reset_domains.*.sync`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.contracts.config import DesignConfig

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
        # 2ff_sync is a backwards-compatible alias, normalized to the
        # canonical name at load time.
        assert cfg.connections[0].cdc == {"kind": "level_sync"}

    def test_valid_level_sync_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: level_sync}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.connections[0].cdc == {"kind": "level_sync"}

    def test_valid_async_fifo_with_depth_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: async_fifo, depth: 4}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.connections[0].cdc == {"kind": "async_fifo", "depth": 4}

    def test_async_fifo_without_depth_raises(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: async_fifo}\n"
        ))
        with pytest.raises(ValueError, match="depth"):
            DesignConfig.load_relaxed(design)

    def test_async_fifo_non_power_of_two_depth_raises(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: async_fifo, depth: 3}\n"
        ))
        with pytest.raises(ValueError, match="power of two"):
            DesignConfig.load_relaxed(design)

    def test_valid_pulse_sync_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: pulse_sync, min_spacing_cycles: 8}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.connections[0].cdc == {"kind": "pulse_sync", "min_spacing_cycles": 8}

    def test_pulse_sync_without_min_spacing_cycles_raises(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: pulse_sync}\n"
        ))
        with pytest.raises(ValueError, match="min_spacing_cycles"):
            DesignConfig.load_relaxed(design)

    def test_pulse_sync_negative_min_spacing_cycles_raises(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: pulse_sync, min_spacing_cycles: -1}\n"
        ))
        with pytest.raises(ValueError, match="min_spacing_cycles"):
            DesignConfig.load_relaxed(design)

    def test_valid_mailbox_transfer_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: mailbox_transfer}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.connections[0].cdc == {"kind": "mailbox_transfer"}

    def test_reset_sync_is_not_a_connection_kind(self, tmp_path):
        # reset_sync is a reset-domain property (reset_domains.*.sync),
        # not a Connection.cdc kind — see forge.contracts.cdc's module
        # docstring for why.
        design = _write(tmp_path, (
            "connections:\n"
            "  - from: src\n"
            "    to: dst\n"
            "    cdc: {kind: reset_sync}\n"
        ))
        with pytest.raises(ValueError, match="kind"):
            DesignConfig.load_relaxed(design)

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
        assert cfg.clock_domains["ap_clk"] == {"derived_from": None, "ratio": None, "sync": None}
        assert cfg.clock_domains["clk_slow"] == {"derived_from": "ap_clk", "ratio": 4, "sync": None}

    def test_no_block_declared_stays_empty(self, tmp_path):
        design = _write(tmp_path, "")
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.clock_domains == {}
        assert cfg.reset_domains == {}

    def test_reset_domains_block_parses_independently(self, tmp_path):
        design = _write(tmp_path, "reset_domains:\n  ap_rst: {}\n")
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.reset_domains == {"ap_rst": {"derived_from": None, "ratio": None, "sync": None}}
        assert cfg.clock_domains == {}

    def test_non_mapping_block_raises(self, tmp_path):
        design = _write(tmp_path, "clock_domains: [not, a, mapping]\n")
        with pytest.raises(ValueError, match="clock_domains"):
            DesignConfig.load_relaxed(design)

    def test_invalid_ratio_raises(self, tmp_path):
        design = _write(tmp_path, "clock_domains:\n  clk_slow: {ratio: -1}\n")
        with pytest.raises(ValueError, match="ratio"):
            DesignConfig.load_relaxed(design)

    def test_reset_domains_sync_reset_sync_with_derived_from_loads_fine(self, tmp_path):
        design = _write(tmp_path, (
            "reset_domains:\n"
            "  rst_slow: {derived_from: ap_rst, sync: reset_sync}\n"
        ))
        cfg = DesignConfig.load_relaxed(design)
        assert cfg.reset_domains["rst_slow"] == {
            "derived_from": "ap_rst", "ratio": None, "sync": "reset_sync",
        }

    def test_reset_domains_sync_without_derived_from_raises(self, tmp_path):
        design = _write(tmp_path, (
            "reset_domains:\n"
            "  rst_slow: {sync: reset_sync}\n"
        ))
        with pytest.raises(ValueError, match="derived_from"):
            DesignConfig.load_relaxed(design)

    def test_reset_domains_invalid_sync_value_raises(self, tmp_path):
        design = _write(tmp_path, (
            "reset_domains:\n"
            "  rst_slow: {derived_from: ap_rst, sync: made_up}\n"
        ))
        with pytest.raises(ValueError, match="reset_sync"):
            DesignConfig.load_relaxed(design)

    def test_clock_domains_sync_is_rejected(self, tmp_path):
        # 'sync' is a reset-only concept — a clock domain has no
        # equivalent synchronizer construct in this release.
        design = _write(tmp_path, (
            "clock_domains:\n"
            "  clk_slow: {derived_from: ap_clk, sync: reset_sync}\n"
        ))
        with pytest.raises(ValueError, match="sync"):
            DesignConfig.load_relaxed(design)
