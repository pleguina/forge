"""Tests for gen_stimulus.py — the trigger_demo xsim stimulus generator.

Validates that:
  * parse_golden_xml() returns 4 events with correct golden data
  * generate_for_module() writes syntactically correct SVH per module
  * Each generated task drives all 4 events
  * Assertion checks reference correct expected values from algorithm
  * dry_run flag skips file writing
  * Unknown module raises ValueError
  * Generated stimulus_current.svh files already exist in repo (committed)
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from gen_stimulus import (  # noqa: E402
    Event,
    GoldenExpected,
    _decoded_hit,
    generate_all,
    generate_for_module,
    parse_golden_xml,
)

# ── Paths ──────────────────────────────────────────────────────────────────

_TOOLS         = Path(__file__).parent.parent.resolve()
_REPO_ROOT     = _TOOLS.parents[4]
_PLUGIN_VERIFY = _TOOLS.parent
_XML           = _REPO_ROOT / "plugins/trigger_demo/forge/verify/schemas/data/trigger_demo_golden.xml"


# ═══════════════════════════════════════════════════════════════════════════
# XML parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestParseGoldenXml:

    def test_returns_four_events(self) -> None:
        events = parse_golden_xml(_XML)
        assert len(events) == 4

    def test_event_ids_sequential(self) -> None:
        events = parse_golden_xml(_XML)
        assert [e.event_id for e in events] == [0, 1, 2, 3]

    def test_each_event_has_four_channels(self) -> None:
        events = parse_golden_xml(_XML)
        for ev in events:
            assert len(ev.channels) == 4, f"event {ev.event_id} has {len(ev.channels)} channels"

    def test_event0_golden_n_hits(self) -> None:
        ev = parse_golden_xml(_XML)[0]
        assert ev.golden is not None
        assert ev.golden.n_hits == 3

    def test_event0_golden_phi_sum(self) -> None:
        ev = parse_golden_xml(_XML)[0]
        assert ev.golden.phi_sum == 0x0213

    def test_event0_golden_accept(self) -> None:
        ev = parse_golden_xml(_XML)[0]
        assert ev.golden.accept == 1

    def test_event1_golden_n_hits(self) -> None:
        ev = parse_golden_xml(_XML)[1]
        assert ev.golden.n_hits == 1

    def test_event1_golden_accept_reject(self) -> None:
        ev = parse_golden_xml(_XML)[1]
        assert ev.golden.accept == 0

    def test_event2_golden_n_hits(self) -> None:
        ev = parse_golden_xml(_XML)[2]
        assert ev.golden.n_hits == 4

    def test_event3_all_zero(self) -> None:
        ev = parse_golden_xml(_XML)[3]
        assert ev.golden.n_hits == 0
        assert ev.golden.phi_sum == 0
        assert ev.golden.accept == 0

    def test_event0_channel0_raw(self) -> None:
        ev = parse_golden_xml(_XML)[0]
        ch0 = next(c for c in ev.channels if c.ch_id == 0)
        assert ch0.raw == 0x00A00050
        assert ch0.valid == 1


# ═══════════════════════════════════════════════════════════════════════════
# Decode helper
# ═══════════════════════════════════════════════════════════════════════════

class TestDecodedHit:

    def test_phi_calibration(self) -> None:
        d_hit, d_valid = _decoded_hit(0x00A00050, 1)
        assert d_hit == 0x00A10050
        assert d_valid == 1

    def test_zero_raw_invalid(self) -> None:
        d_hit, d_valid = _decoded_hit(0x00000000, 0)
        assert d_valid == 0

    def test_zero_raw_valid_false(self) -> None:
        """raw_valid=1 but raw_hit=0 → decoded_valid=0 (and condition)."""
        _d_hit, d_valid = _decoded_hit(0x00000000, 1)
        assert d_valid == 0

    def test_max_channels_event2(self) -> None:
        d_hit, d_valid = _decoded_hit(0x01000100, 1)
        # phi=0x0100, phi+1=0x0101, eta=0x0100
        assert d_hit == 0x01010100
        assert d_valid == 1


# ═══════════════════════════════════════════════════════════════════════════
# generate_for_module
# ═══════════════════════════════════════════════════════════════════════════

class TestGenerateForModule:

    @pytest.fixture()
    def events(self) -> list[Event]:
        return parse_golden_xml(_XML)

    def test_writes_file(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("hit_decoder", events, out)
        assert out.exists()

    def test_dry_run_no_file(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("hit_decoder", events, out, dry_run=True)
        assert not out.exists()

    def test_unknown_module_raises(self, events: list[Event], tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown module"):
            generate_for_module("bogus_module", events, tmp_path / "x.svh")

    @pytest.mark.parametrize("module", [
        "hit_decoder", "hit_collector", "trigger_logic", "trigger_output", "trigger_pipeline"
    ])
    def test_all_supported_modules_write(self, module: str, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module(module, events, out)
        assert out.exists()
        content = out.read_text()
        assert "task automatic run_stimulus" in content
        assert "endtask" in content

    def test_hit_decoder_contains_golden_value(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("hit_decoder", events, out)
        content = out.read_text()
        # Event 0, ch0: decoded_hit expected = 0x00A10050
        assert "00A10050" in content

    def test_hit_decoder_covers_all_events(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("hit_decoder", events, out)
        content = out.read_text()
        for ev_id in range(4):
            assert f"E{ev_id}.ch" in content, f"Event {ev_id} not in stimulus"

    def test_hit_collector_n_hits_check(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("hit_collector", events, out)
        content = out.read_text()
        # Event 0 n_hits=3
        assert "n_hits !== 3'd3" in content or "3'd3" in content

    def test_hit_collector_phi_sum_check(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("hit_collector", events, out)
        content = out.read_text()
        assert "0213" in content  # phi_sum for event 0

    def test_trigger_logic_accept_check(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("trigger_logic", events, out)
        content = out.read_text()
        # Event 0: n_hits=3 >= 2 → accept=1
        assert "trigger_accept" in content
        assert "1'b1" in content

    def test_trigger_output_word_check(self, events: list[Event], tmp_path: Path) -> None:
        out = tmp_path / "stimulus_current.svh"
        generate_for_module("trigger_output", events, out)
        content = out.read_text()
        # Event 0: word=0x80030000
        assert "80030000" in content

    def test_uses_fatal_not_error(self, events: list[Event], tmp_path: Path) -> None:
        """All modules must use $fatal(1,...) for failure signaling."""
        for module in ("hit_decoder", "hit_collector", "trigger_logic", "trigger_output", "trigger_pipeline"):
            out = tmp_path / f"{module}_stim.svh"
            generate_for_module(module, events, out)
            content = out.read_text()
            assert "$fatal(1," in content, f"{module}: no $fatal(1,...) found"


# ═══════════════════════════════════════════════════════════════════════════
# generate_all
# ═══════════════════════════════════════════════════════════════════════════

class TestGenerateAll:

    def test_generates_five_files(self, tmp_path: Path) -> None:
        # Point to an isolated verify_root with flow subdirs
        for mod in ("hit_decoder_xsim", "hit_collector_xsim",
                    "trigger_logic_xsim", "trigger_output_xsim",
                    "trigger_pipeline_xsim"):
            (tmp_path / mod).mkdir(parents=True)
        results = generate_all(xml_path=_XML, verify_root=tmp_path)
        assert len(results) == 5

    def test_module_filter(self, tmp_path: Path) -> None:
        (tmp_path / "hit_decoder_xsim").mkdir(parents=True)
        results = generate_all(
            xml_path=_XML,
            verify_root=tmp_path,
            module_filter="hit_decoder",
        )
        assert len(results) == 1

    def test_dry_run_produces_no_files(self, tmp_path: Path) -> None:
        results = generate_all(
            xml_path=_XML,
            verify_root=tmp_path,
            dry_run=True,
        )
        assert len(results) == 5
        for p in results:
            assert not p.exists()


# ═══════════════════════════════════════════════════════════════════════════
# Pre-generated artifacts in repo
# ═══════════════════════════════════════════════════════════════════════════

class TestPreGeneratedStimulus:
    """Verify committed stimulus_current.svh files are present and correct."""

    _TB_FILES = {
        "hit_decoder_xsim": "tb_hit_decoder_xsim.sv",
        "hit_collector_xsim": "tb_hit_collector_xsim.sv",
        "trigger_logic_xsim": "tb_trigger_logic_xsim.sv",
        "trigger_output_xsim": "tb_trigger_output_xsim.sv",
        "trigger_pipeline_xsim": "tb_algo_top.sv",
    }

    @pytest.mark.parametrize("flow", [
        "hit_decoder_xsim",
        "hit_collector_xsim",
        "trigger_logic_xsim",
        "trigger_output_xsim",
        "trigger_pipeline_xsim",
    ])
    def test_stimulus_file_exists(self, flow: str) -> None:
        svh = _PLUGIN_VERIFY / flow / "stimulus_current.svh"
        assert svh.exists(), (
            f"stimulus_current.svh missing for {flow}\n"
            f"Run: python3 plugins/trigger_demo/verify/tools/gen_stimulus.py"
        )

    @pytest.mark.parametrize("flow", [
        "hit_decoder_xsim",
        "hit_collector_xsim",
        "trigger_logic_xsim",
        "trigger_output_xsim",
        "trigger_pipeline_xsim",
    ])
    def test_stimulus_has_run_stimulus_task(self, flow: str) -> None:
        svh = _PLUGIN_VERIFY / flow / "stimulus_current.svh"
        if not svh.exists():
            pytest.skip("stimulus_current.svh not generated yet")
        assert "task automatic run_stimulus" in svh.read_text()

    @pytest.mark.parametrize("flow", [
        "hit_decoder_xsim",
        "hit_collector_xsim",
        "trigger_logic_xsim",
        "trigger_output_xsim",
        "trigger_pipeline_xsim",
    ])
    def test_sv_calls_run_stimulus(self, flow: str) -> None:
        """The generated TB SV must contain the run_stimulus() call."""
        tb_sv = _PLUGIN_VERIFY / flow / self._TB_FILES[flow]
        if not tb_sv.exists():
            pytest.skip("TB SV not generated yet")
        assert "run_stimulus()" in tb_sv.read_text()
