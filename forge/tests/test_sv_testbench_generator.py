"""Coverage for SVTestbenchGenerator / generate_sv_testbench.

This is the testbench generator wired into `forge topgen gen-top --mode
verilog --gen-testbench` (forge/core/cli/groups/topgen.py:2140) — a real,
gated-behind-a-flag CLI feature, not dead code — but it measured 5%
covered as of 2026-09-17, the file the most recent shipped bugfix
(35b7c6e, "Fix UnboundLocalError in xml_to_sv_stimulus tool discovery")
touched.

Two of its port_map.yaml schema branches (channel groups using the
`channels:`/`stimulus_groups:`/`groups:` keys, read by
_all_channel_groups/_config_stimulus_groups/_grouped_input_groups) are
exercised below with hand-built fixtures because no currently-generated
port_map.yaml in this repo's reference plugins (passthrough_demo,
trigger_demo, vision_pipeline_demo) actually produces that shape — real
generated port_map.yaml files only ever populate the
clock_reset/bx0/outputs/unclassified groups. The branch is real,
reachable code, just not currently exercised by any generated fixture in
this repo.

A real, previously-live gap was found while writing this suite: the
checked-in gen-top/design_passthrough_demo/ reference build's
probe_map.yaml never declared the cycle_in_bx Tier 2 probe this
generator required, so `forge topgen gen-top --mode verilog
--gen-testbench` run fresh against it raised ValueError instead of
regenerating tb_algo_top.sv. Root cause: passthrough_demo has no
bx_timing_bx0_global port at all — it's a generic algorithm passthrough,
not a BX-timed (OMTF/LHC-style) design — but the generator required the
whole cycle_in_bx alignment machinery unconditionally, regardless of
whether the design had any BX timing concept to align to.
_write_testbench now gates every bx_timing_bx0_global/cycle_in_bx
reference (drive_all_idle, the reset sequence, logging fmt/CSV header,
the BX-alignment wait loop, the stimulus loop's BX0 pulse, drain) behind
`has_bx_timing = self._has_port('bx_timing_bx0_global')`, computed once
at the top of the method — a BX-timed design's generated output is
byte-for-byte unchanged (every gated line's content is identical, only
now conditional), while a non-BX design like passthrough_demo gets a
testbench with no BX machinery in it at all instead of a crash.
test_real_passthrough_artifacts_regenerate_testbench is the regression
test for this fix, run against that real checked-in fixture, not a
synthetic one.

The former `_port_group`/`_channel_group` private helpers were deleted
as part of this same pass — confirmed dead (never called from anywhere
in this class or file) while investigating the above.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from forge.generation.generators.sv_testbench_generator import (
    SVTestbenchGenerator,
    generate_sv_testbench,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PASSTHROUGH_DIR = REPO_ROOT / "gen-top" / "design_passthrough_demo"


# --------------------------------------------------------------------------- fixture builders

def _write_params(tmp_path: Path, *, batches=4, clock_period_ns=4.0, algo_freq_mhz=250.0) -> None:
    params = {
        "timing": {
            "clock_period_ns": clock_period_ns,
            "algo_freq_mhz": algo_freq_mhz,
            "batches_per_event": batches,
        }
    }
    (tmp_path / "design_parameters.json").write_text(json.dumps(params))


def _write_port_map(tmp_path: Path, port_groups: dict, tier2_probes=None) -> None:
    data = {
        "format_version": "1",
        "top_module": "algo_top",
        "port_signature_hash": "deadbeef",
        "port_groups": port_groups,
        "tier2_probes": tier2_probes or [],
    }
    (tmp_path / "port_map.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def _write_probe_map(tmp_path: Path, tier2_probes) -> None:
    data = {"probe_tiers": {"tier1": [], "tier2": tier2_probes}}
    (tmp_path / "probe_map.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def _make_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


MINIMAL_PORT_GROUPS = {
    "clock_reset": [],
    "bx0": [],
    "outputs": [
        {"name": "out_valid", "direction": "output", "width": 1},
        {"name": "out_word", "direction": "output", "width": 32},
    ],
    "unclassified": [
        {"name": "ap_clk", "direction": "input", "width": 1},
        {"name": "ap_rst", "direction": "input", "width": 1},
    ],
}

MINIMAL_PORT_GROUPS_WITH_CYCLE_PORT = {
    **MINIMAL_PORT_GROUPS,
    "unclassified": MINIMAL_PORT_GROUPS["unclassified"]
    + [{"name": "bx_timing_cycle_in_bx", "direction": "input", "width": 4}],
}

# A design that exposes the global BX0 pulse -- i.e. an actual BX-timed
# (OMTF/LHC-style) design, as opposed to a generic algorithm passthrough.
# Only these designs require/get the cycle_in_bx alignment machinery.
BX_TIMED_PORT_GROUPS = {
    **MINIMAL_PORT_GROUPS,
    "unclassified": MINIMAL_PORT_GROUPS["unclassified"]
    + [{"name": "bx_timing_bx0_global", "direction": "input", "width": 1}],
}

RICH_PORT_GROUPS = {
    "clock_reset": [],
    "bx0": [],
    "outputs": [
        {"name": "out_valid", "direction": "output", "width": 1},
        {"name": "out_word", "direction": "output", "width": 32},
    ],
    "unclassified": [
        {"name": "ap_clk", "direction": "input", "width": 1},
        {"name": "ap_rst", "direction": "input", "width": 1},
    ],
    "stream_in": {
        "channels": [
            {"name": "strm_0", "width": 16},
            {"name": "strm_1", "width": 16},
        ],
        "channel_width": 16,
        "stimulus_prefix": "STREAM",
    },
    "cfg_top": {
        "width": 64,
        "stimulus_groups": [
            {
                "name": "slr_config",
                "stimulus_prefix": "CFG",
                "ports": [{"name": "cfg_reg_0"}, {"name": "cfg_reg_1"}],
            }
        ],
    },
    "raw_hits": {
        "groups": [
            {
                "name": "dec_hits",
                "ports": [
                    {"name": "dec_0_raw_hit", "width": 32},
                    {"name": "dec_1_raw_hit", "width": 32},
                ],
            }
        ],
    },
}

RICH_TIER2_PROBES = [
    {"name": "cycle_in_bx", "net": "internal_cycle_ctr", "width": 4},
    {"name": "strm_0_dbg", "net": "strm_0", "width": 16},
]


@pytest.fixture()
def minimal_generator(tmp_path) -> SVTestbenchGenerator:
    _write_params(tmp_path)
    _write_port_map(tmp_path, MINIMAL_PORT_GROUPS_WITH_CYCLE_PORT)
    return SVTestbenchGenerator(tmp_path / "algo_top.v")


@pytest.fixture()
def rich_generator(tmp_path) -> SVTestbenchGenerator:
    _write_params(tmp_path, batches=3)
    _write_port_map(tmp_path, RICH_PORT_GROUPS)
    _write_probe_map(tmp_path, RICH_TIER2_PROBES)
    return SVTestbenchGenerator(tmp_path / "algo_top.v")


# --------------------------------------------------------------------------- __init__

def test_init_missing_design_parameters_raises(tmp_path):
    _write_port_map(tmp_path, MINIMAL_PORT_GROUPS)
    with pytest.raises(FileNotFoundError, match="design_parameters.json"):
        SVTestbenchGenerator(tmp_path / "algo_top.v")


def test_init_missing_port_map_raises(tmp_path):
    _write_params(tmp_path)
    with pytest.raises(FileNotFoundError, match="port_map.yaml"):
        SVTestbenchGenerator(tmp_path / "algo_top.v")


def test_init_port_map_without_port_groups_raises(tmp_path):
    _write_params(tmp_path)
    (tmp_path / "port_map.yaml").write_text(yaml.safe_dump({"port_groups": {}}))
    with pytest.raises(ValueError, match="port_groups"):
        SVTestbenchGenerator(tmp_path / "algo_top.v")


def test_init_probe_map_absent_is_none(minimal_generator):
    assert minimal_generator.probe_map is None


def test_init_probe_map_loaded_when_present(rich_generator):
    assert rich_generator.probe_map is not None
    assert rich_generator.probe_map["probe_tiers"]["tier2"] == RICH_TIER2_PROBES


# --------------------------------------------------------------------------- _output_ports / _has_port

def test_output_ports_from_grouped_port_map(minimal_generator):
    names = [p["name"] for p in minimal_generator._output_ports()]
    assert names == ["out_valid", "out_word"]


def test_output_ports_flat_fallback(minimal_generator):
    minimal_generator.port_map = {"ports": [
        {"name": "flat_out", "direction": "output", "width": 8},
        {"name": "flat_in", "direction": "input", "width": 8},
    ]}
    assert [p["name"] for p in minimal_generator._output_ports()] == ["flat_out"]


def test_output_ports_none_port_map(minimal_generator):
    minimal_generator.port_map = None
    assert minimal_generator._output_ports() == []


def test_has_port_true_and_false(minimal_generator):
    assert minimal_generator._has_port("ap_clk") is True
    assert minimal_generator._has_port("nonexistent") is False


def test_has_port_flat_fallback(minimal_generator):
    minimal_generator.port_map = {"ports": [{"name": "flat_sig"}]}
    assert minimal_generator._has_port("flat_sig") is True


def test_has_port_none_port_map(minimal_generator):
    minimal_generator.port_map = None
    assert minimal_generator._has_port("anything") is False


# --------------------------------------------------------------------------- tier2 probes / port_group helpers

def test_tier2_probes_from_probe_map_takes_precedence(rich_generator):
    assert rich_generator._tier2_probes() == RICH_TIER2_PROBES


def test_tier2_probes_fallback_to_port_map(tmp_path):
    _write_params(tmp_path)
    fallback_probes = [{"name": "cycle_in_bx", "net": "ctr", "width": 1}]
    _write_port_map(tmp_path, MINIMAL_PORT_GROUPS, tier2_probes=fallback_probes)
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    assert gen._tier2_probes() == fallback_probes


def test_tier2_probes_empty_when_absent(minimal_generator):
    assert minimal_generator._tier2_probes() == []


def test_tier2_probes_empty_when_port_map_none(minimal_generator):
    minimal_generator.port_map = None
    assert minimal_generator._tier2_probes() == []


# --------------------------------------------------------------------------- config / grouped-input / channel groups

def test_config_stimulus_groups_defaults(rich_generator):
    groups = rich_generator._config_stimulus_groups()
    assert len(groups) == 1
    g = groups[0]
    assert g["name"] == "slr_config"
    assert g["stimulus_prefix"] == "CFG"
    assert g["width"] == 64  # inherited from parent cfg_top.width
    assert g["parent_group"] == "cfg_top"
    assert [p["name"] for p in g["ports"]] == ["cfg_reg_0", "cfg_reg_1"]


def test_config_stimulus_groups_empty_on_minimal(minimal_generator):
    assert minimal_generator._config_stimulus_groups() == []


def test_grouped_input_groups_defaults_prefix(rich_generator):
    groups = rich_generator._grouped_input_groups()
    assert len(groups) == 1
    g = groups[0]
    assert g["name"] == "dec_hits"
    assert g["parent_group"] == "raw_hits"
    assert g["stimulus_prefix"] == "DEC_HITS"  # defaulted from name.upper()


def test_all_channel_groups_skips_reserved_names_and_reads_shape(rich_generator):
    groups = rich_generator._all_channel_groups()
    names = [g[0] for g in groups]
    assert names == ["stream_in"]
    _name, channels, width, prefix = groups[0]
    assert [c["name"] for c in channels] == ["strm_0", "strm_1"]
    assert width == 16
    assert prefix == "STREAM"


def test_all_channel_groups_skips_non_dict_group_values(tmp_path):
    _write_params(tmp_path)
    groups = dict(MINIMAL_PORT_GROUPS)
    groups["stray_list_group"] = ["not", "a", "dict"]
    _write_port_map(tmp_path, groups)
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    assert gen._all_channel_groups() == []


def test_all_channel_groups_default_prefix_when_unset(tmp_path):
    _write_params(tmp_path)
    groups = dict(MINIMAL_PORT_GROUPS)
    groups["my_channels"] = {"channels": [{"name": "c0", "width": 8}]}
    _write_port_map(tmp_path, groups)
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    _name, _channels, _width, prefix = gen._all_channel_groups()[0]
    assert prefix == "MY_CHANNELS_STIM"


# --------------------------------------------------------------------------- _find_probe

def test_find_probe_from_probe_map(rich_generator):
    probe = rich_generator._find_probe("cycle_in_bx")
    assert probe["net"] == "internal_cycle_ctr"


def test_find_probe_fallback_to_port_map(tmp_path):
    _write_params(tmp_path)
    _write_port_map(tmp_path, MINIMAL_PORT_GROUPS, tier2_probes=[{"name": "x", "net": "y", "width": 1}])
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    assert gen._find_probe("x")["net"] == "y"


def test_find_probe_not_found(minimal_generator):
    assert minimal_generator._find_probe("nope") is None


# --------------------------------------------------------------------------- _find_converter_tool

def test_find_converter_tool_explicit_path(tmp_path, minimal_generator):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(tool, "#!/bin/sh\nexit 0\n")
    minimal_generator.stimulus_converter_path = tool.resolve()
    assert minimal_generator._find_converter_tool() == tool.resolve()


def test_find_converter_tool_env_var(tmp_path, minimal_generator, monkeypatch):
    tool = tmp_path / "env_tool"
    _make_executable(tool, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("TOPGEN_XML_TO_SV_STIMULUS", str(tool))
    assert minimal_generator._find_converter_tool() == tool.resolve()


def test_find_converter_tool_on_path(tmp_path, minimal_generator, monkeypatch):
    tool = tmp_path / "path_tool"
    _make_executable(tool, "#!/bin/sh\nexit 0\n")
    monkeypatch.delenv("TOPGEN_XML_TO_SV_STIMULUS", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: str(tool) if name == "xml_to_sv_stimulus" else None)
    assert minimal_generator._find_converter_tool() == tool.resolve()


def test_find_converter_tool_not_executable_is_skipped(tmp_path, minimal_generator):
    tool = tmp_path / "not_exec"
    tool.write_text("not executable")
    minimal_generator.stimulus_converter_path = tool.resolve()
    assert minimal_generator._find_converter_tool() is None


def test_find_converter_tool_none_found(minimal_generator, monkeypatch):
    monkeypatch.delenv("TOPGEN_XML_TO_SV_STIMULUS", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert minimal_generator._find_converter_tool() is None


# --------------------------------------------------------------------------- _generate_stimulus_include

def test_generate_stimulus_include_no_converter_returns_none(tmp_path, minimal_generator, monkeypatch):
    monkeypatch.delenv("TOPGEN_XML_TO_SV_STIMULUS", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = minimal_generator._generate_stimulus_include(tmp_path / "in.xml", tmp_path, event_id=1)
    assert result is None


def test_generate_stimulus_include_success(tmp_path, minimal_generator):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(
        tool,
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do case $1 in --output) OUT=$2;; esac; shift; done\n"
        "echo 'real stimulus content' > \"$OUT\"\n"
        "echo converted\n",
    )
    minimal_generator.stimulus_converter_path = tool.resolve()
    xml_path = tmp_path / "in.xml"
    xml_path.write_text("<events/>")
    out = tmp_path / "out"
    out.mkdir()
    result = minimal_generator._generate_stimulus_include(xml_path, out, event_id=7)
    assert result == out / "stimulus_event_7.svh"
    assert result.read_text().strip() == "real stimulus content"


def test_generate_stimulus_include_tool_fails_returns_none(tmp_path, minimal_generator):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(tool, "#!/bin/sh\necho boom >&2\nexit 1\n")
    minimal_generator.stimulus_converter_path = tool.resolve()
    result = minimal_generator._generate_stimulus_include(tmp_path / "in.xml", tmp_path, event_id=1)
    assert result is None


def test_generate_stimulus_include_tool_fails_with_stdout_and_stderr(tmp_path, minimal_generator):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(tool, "#!/bin/sh\necho some-stdout\necho some-stderr >&2\nexit 1\n")
    minimal_generator.stimulus_converter_path = tool.resolve()
    result = minimal_generator._generate_stimulus_include(tmp_path / "in.xml", tmp_path, event_id=1)
    assert result is None


def test_generate_stimulus_include_tool_runs_but_no_output_file(tmp_path, minimal_generator):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(tool, "#!/bin/sh\nexit 0\n")  # succeeds, writes nothing
    minimal_generator.stimulus_converter_path = tool.resolve()
    result = minimal_generator._generate_stimulus_include(tmp_path / "in.xml", tmp_path, event_id=1)
    assert result is None


def test_generate_stimulus_include_unexpected_exception_returns_none(tmp_path, minimal_generator, monkeypatch):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(tool, "#!/bin/sh\nexit 0\n")
    minimal_generator.stimulus_converter_path = tool.resolve()

    def _boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("subprocess.run", _boom)
    result = minimal_generator._generate_stimulus_include(tmp_path / "in.xml", tmp_path, event_id=1)
    assert result is None


# --------------------------------------------------------------------------- _generate_placeholder_stimulus

def test_generate_placeholder_stimulus_content(tmp_path, rich_generator):
    out = rich_generator._generate_placeholder_stimulus(tmp_path, event_id=2)
    text = out.read_text()
    assert out.name == "stimulus_event_2.svh"
    assert "localparam int NUM_BATCHES = 3;" in text
    # config group: 2 ports -> CFG_0 / CFG_1 localparams at width 64
    assert "localparam logic [63:0] CFG_0 = 64'h0;" in text
    assert "localparam logic [63:0] CFG_1 = 64'h0;" in text
    # simple channel group: array declarations sized to NUM_BATCHES
    assert "const logic [15:0] STREAM_0[NUM_BATCHES] = '{" in text
    assert "const logic [15:0] STREAM_1[NUM_BATCHES] = '{" in text
    # grouped input group
    assert "const logic [31:0] DEC_HITS_0[NUM_BATCHES] = '{" in text
    assert text.count("'h1") == 2 * 3  # 2 stream channels * 3 batches


def test_generate_placeholder_stimulus_skips_empty_grouped_input(tmp_path):
    _write_params(tmp_path, batches=2)
    groups = dict(MINIMAL_PORT_GROUPS)
    groups["raw_hits"] = {"groups": [{"name": "empty_group", "ports": []}]}
    _write_port_map(tmp_path, groups)
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    out = gen._generate_placeholder_stimulus(tmp_path, event_id=1)
    assert "EMPTY_GROUP" not in out.read_text()


def test_generate_placeholder_stimulus_minimal_has_no_groups(tmp_path, minimal_generator):
    out = minimal_generator._generate_placeholder_stimulus(tmp_path, event_id=1)
    text = out.read_text()
    assert "NUM_BATCHES = 4" in text
    assert "STIM" not in text


# --------------------------------------------------------------------------- generate_testbench (integration)

def test_generate_testbench_minimal_placeholder_path(tmp_path, minimal_generator):
    out_dir = tmp_path / "out"
    tb_path = minimal_generator.generate_testbench(out_dir)

    assert tb_path == out_dir / "tb_algo_top.sv"
    text = tb_path.read_text()
    assert "module tb_algo_top;" in text
    assert 'include "tb_bindings.svh"' in text
    assert 'include "stimulus_current.svh"' in text
    assert "algo_top dut (.*);" in text
    assert "localparam real CLK_PERIOD_NS            = 4.0;" in text
    assert "Stimulus data: Placeholder" in text
    # bx_timing_cycle_in_bx is a real top-level port here, but this fixture
    # has no bx_timing_bx0_global port at all -> not a BX-timed design ->
    # no BX alignment/alias machinery at all, and no bx0 log column.
    assert "generated probe-map alias" not in text
    assert "bx_timing_cycle_in_bx" not in text
    assert '$fwrite(out_csv_fd, "cycle,out_valid,out_word' in text
    assert (out_dir / "stimulus_event_1.svh").exists()


def test_generate_testbench_cycle_alias_multibit(tmp_path):
    _write_params(tmp_path)
    _write_port_map(tmp_path, BX_TIMED_PORT_GROUPS)  # bx0 present, no cycle_in_bx port
    _write_probe_map(tmp_path, [{"name": "cycle_in_bx", "net": "ctr_reg", "width": 4}])
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")

    tb_path = gen.generate_testbench(tmp_path / "out")
    text = tb_path.read_text()
    assert "wire [3:0] bx_timing_cycle_in_bx = ctr_reg;" in text
    assert '$fwrite(out_csv_fd, "cycle,bx0,out_valid,out_word' in text


def test_generate_testbench_cycle_alias_single_bit(tmp_path):
    _write_params(tmp_path)
    _write_port_map(tmp_path, BX_TIMED_PORT_GROUPS)
    _write_probe_map(tmp_path, [{"name": "cycle_in_bx", "net": "ctr_bit", "width": 1}])
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")

    tb_path = gen.generate_testbench(tmp_path / "out")
    text = tb_path.read_text()
    assert "wire bx_timing_cycle_in_bx = ctr_bit;" in text


def test_generate_testbench_non_bx_design_needs_no_cycle_probe(tmp_path):
    """A design with no bx_timing_bx0_global port needs no cycle_in_bx
    probe at all -- this is the non-BX path, not an error path."""
    _write_params(tmp_path)
    _write_port_map(tmp_path, MINIMAL_PORT_GROUPS)  # no bx0 port, no probe anywhere
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    tb_path = gen.generate_testbench(tmp_path / "out")
    assert "cycle_in_bx" not in tb_path.read_text()


def test_generate_testbench_missing_cycle_probe_raises(tmp_path):
    _write_params(tmp_path)
    _write_port_map(tmp_path, BX_TIMED_PORT_GROUPS)  # bx0 present, no port, no probe
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    with pytest.raises(ValueError, match="missing the cycle_in_bx Tier 2 probe"):
        gen.generate_testbench(tmp_path / "out")


def test_generate_testbench_cycle_probe_missing_net_raises(tmp_path):
    _write_params(tmp_path)
    _write_port_map(tmp_path, BX_TIMED_PORT_GROUPS)
    _write_probe_map(tmp_path, [{"name": "cycle_in_bx", "width": 1}])  # no 'net'
    gen = SVTestbenchGenerator(tmp_path / "algo_top.v")
    with pytest.raises(ValueError, match="missing its net binding"):
        gen.generate_testbench(tmp_path / "out")


def test_generate_testbench_rich_fixture_bodies(tmp_path, rich_generator):
    tb_path = rich_generator.generate_testbench(tmp_path / "out")
    text = tb_path.read_text()

    # apply_configs drives CFG_N into the real config port names
    assert "cfg_reg_0 = CFG_0; cfg_reg_1 = CFG_1;" in text
    # apply_batch drives channel + grouped-input stimulus arrays
    assert "strm_0 = STREAM_0[batch_idx]; strm_1 = STREAM_1[batch_idx];" in text
    assert "dec_0_raw_hit = DEC_HITS_0[batch_idx]; dec_1_raw_hit = DEC_HITS_1[batch_idx];" in text
    # drive_all_idle covers the channel group's own ports
    assert "strm_0 = '0; strm_1 = '0;" in text
    # tier2 probe capture line references the real net for a >1-bit probe
    assert '$fwrite(probe_csv_fd, "%0d,%0h,%0h\\n", cycle_count, internal_cycle_ctr, strm_0);' in text


def test_generate_testbench_with_real_stimulus_tool(tmp_path, minimal_generator):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(
        tool,
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do case $1 in --output) OUT=$2;; esac; shift; done\n"
        "echo '// real xml-derived stimulus' > \"$OUT\"\n",
    )
    minimal_generator.stimulus_converter_path = tool.resolve()
    xml_path = tmp_path / "event.xml"
    xml_path.write_text("<events/>")

    out_dir = tmp_path / "out"
    tb_path = minimal_generator.generate_testbench(out_dir, xml_stimulus_path=xml_path, event_id=3)
    assert "Stimulus data: C++ tool (xml_to_sv_stimulus)" in tb_path.read_text()
    assert (out_dir / "stimulus_event_3.svh").read_text().strip() == "// real xml-derived stimulus"


def test_generate_testbench_xml_given_but_converter_fails_falls_back(tmp_path, minimal_generator):
    tool = tmp_path / "xml_to_sv_stimulus"
    _make_executable(tool, "#!/bin/sh\nexit 1\n")
    minimal_generator.stimulus_converter_path = tool.resolve()
    xml_path = tmp_path / "event.xml"
    xml_path.write_text("<events/>")

    tb_path = minimal_generator.generate_testbench(tmp_path / "out", xml_stimulus_path=xml_path)
    assert "Stimulus data: Placeholder" in tb_path.read_text()


def test_generate_testbench_xml_path_missing_uses_placeholder(tmp_path, minimal_generator):
    tb_path = minimal_generator.generate_testbench(
        tmp_path / "out", xml_stimulus_path=tmp_path / "does_not_exist.xml"
    )
    assert "Stimulus data: Placeholder" in tb_path.read_text()


# --------------------------------------------------------------------------- generate_sv_testbench (wrapper)

def test_generate_sv_testbench_wrapper_delegates(tmp_path):
    _write_params(tmp_path)
    _write_port_map(tmp_path, MINIMAL_PORT_GROUPS_WITH_CYCLE_PORT)
    tb_path = generate_sv_testbench(tmp_path / "algo_top.v", tmp_path / "out")
    assert tb_path == tmp_path / "out" / "tb_algo_top.sv"
    assert tb_path.exists()


# --------------------------------------------------------------------------- real, checked-in artifacts

@pytest.mark.skipif(
    not (REAL_PASSTHROUGH_DIR / "port_map.yaml").exists(),
    reason="gen-top/design_passthrough_demo reference build not present",
)
def test_real_passthrough_artifacts_regenerate_testbench(tmp_path):
    """Regression test for a real, previously-live gap (see module
    docstring): passthrough_demo has no bx_timing_bx0_global port, so it's
    not a BX-timed design, and used to hit the unconditional cycle_in_bx
    requirement anyway -- `forge topgen gen-top --mode verilog
    --gen-testbench` raised ValueError against this exact checked-in
    reference build. Now that BX machinery is gated on has_bx_timing, this
    real, non-BX design regenerates its testbench successfully.
    """
    gen = SVTestbenchGenerator(REAL_PASSTHROUGH_DIR / "algo_top.v")
    tb_path = gen.generate_testbench(tmp_path / "out")
    text = tb_path.read_text()
    assert "module tb_algo_top;" in text
    assert "algo_top dut (.*);" in text
    assert "bx_timing" not in text
