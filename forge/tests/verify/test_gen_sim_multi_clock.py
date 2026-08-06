"""Tests for multi-clock-domain testbench generation in
forge.verify.gen_sim.render_tb_sv.

Every pre-existing flow declares no extra_clocks/extra_resets at all — the
first class of tests here locks in that the single-clock path is completely
unaffected. The second class covers the new multi-clock rendering itself:
free-running extra clocks at their own declared period, and extra resets
that assert then synchronously deassert against *their own* paired clock
(not ap_clk), matching what a real reset_sync/cdc_reset_sync crossing
requires.
"""
from __future__ import annotations

from forge.verify.gen_sim import render_tb_sv


def _render(**overrides):
    kwargs = dict(
        flow_name="test_flow",
        top_module="algo_top",
        tb_module="tb_algo_top",
        clk_period_ns=4.0,
        reset_cycles=4,
        idle_cycles_after_reset=0,
        post_stimulus_drain_cycles=8,
        port_map_path=None,
    )
    kwargs.update(overrides)
    return render_tb_sv(**kwargs)


class TestSingleClockUnaffected:
    def test_no_extra_clocks_declares_only_ap_clk(self):
        text = _render()
        assert "logic ap_clk;" in text
        assert "logic ap_rst;" in text
        assert "always #(CLK_PERIOD_NS / 2.0) ap_clk = ~ap_clk;" in text
        # No additional-clock-domain section should be rendered at all.
        assert "Additional clock domains" not in text
        assert "Additional reset domains" not in text

    def test_empty_dict_same_as_none(self):
        assert _render(extra_clocks={}, extra_resets={}) == _render()


class TestExtraClocks:
    def test_extra_clock_gets_its_own_free_running_toggle(self):
        text = _render(extra_clocks={"clk_pixel": 5.0})
        assert "logic clk_pixel;" in text
        assert "initial clk_pixel = 1'b0;" in text
        assert "always #(5.0 / 2.0) clk_pixel = ~clk_pixel;" in text

    def test_multiple_extra_clocks_each_get_their_own_period(self):
        text = _render(extra_clocks={"clk_pixel": 5.0, "clk_output": 8.0, "clk_control": 20.0})
        assert "always #(5.0 / 2.0) clk_pixel = ~clk_pixel;" in text
        assert "always #(8.0 / 2.0) clk_output = ~clk_output;" in text
        assert "always #(20.0 / 2.0) clk_control = ~clk_control;" in text

    def test_extra_clock_not_double_declared_as_plain_signal(self):
        """A port_map entry named clk_pixel must not ALSO get a plain
        `logic clk_pixel;` from _render_signal_declarations — that would be
        a duplicate declaration (a real Verilog compile error)."""
        raw_ports = {"port_groups": {"unclassified": [
            {"name": "clk_pixel", "direction": "input", "width": 1},
            {"name": "data_in", "direction": "input", "width": 8},
        ]}}
        import tempfile
        from pathlib import Path
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            pm_path = Path(tmp) / "port_map.yaml"
            pm_path.write_text(yaml.safe_dump(raw_ports))
            text = _render(extra_clocks={"clk_pixel": 5.0}, port_map_path=pm_path)
        assert text.count("logic clk_pixel;") == 1  # only the extra-clock declaration
        assert "logic [7:0] data_in;" in text


class TestExtraResets:
    def test_extra_reset_deasserts_against_its_paired_clock(self):
        text = _render(extra_clocks={"clk_pixel": 5.0}, extra_resets={"rst_pixel": "clk_pixel"})
        assert "logic rst_pixel;" in text
        assert "rst_pixel = 1'b1;" in text
        assert "repeat (RESET_CYCLES) @(posedge clk_pixel);" in text
        assert "rst_pixel = 1'b0;" in text

    def test_reset_with_unknown_clock_name_falls_back_to_ap_clk(self):
        text = _render(extra_resets={"rst_orphan": "clk_nonexistent"})
        assert "logic rst_orphan;" in text
        assert "repeat (RESET_CYCLES) @(posedge ap_clk);" in text
        assert "@(posedge clk_nonexistent);" not in text

    def test_multiple_reset_domains_each_get_independent_sequences(self):
        text = _render(
            extra_clocks={"clk_pixel": 5.0, "clk_output": 8.0},
            extra_resets={"rst_pixel": "clk_pixel", "rst_output": "clk_output"},
        )
        assert "repeat (RESET_CYCLES) @(posedge clk_pixel);" in text
        assert "repeat (RESET_CYCLES) @(posedge clk_output);" in text
        assert text.count("initial begin") >= 2  # one reset sequence per domain


class TestReservedNamesNotZeroed:
    def test_extra_clock_and_reset_not_included_in_input_zeroing(self):
        raw_ports = {"port_groups": {"unclassified": [
            {"name": "clk_pixel", "direction": "input", "width": 1},
            {"name": "rst_pixel", "direction": "input", "width": 1},
            {"name": "data_in", "direction": "input", "width": 8},
        ]}}
        import tempfile
        from pathlib import Path
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            pm_path = Path(tmp) / "port_map.yaml"
            pm_path.write_text(yaml.safe_dump(raw_ports))
            text = _render(
                extra_clocks={"clk_pixel": 5.0},
                extra_resets={"rst_pixel": "clk_pixel"},
                port_map_path=pm_path,
            )
        assert "clk_pixel = '0;" not in text
        assert "rst_pixel = '0;" not in text
        assert "data_in = '0;" in text
