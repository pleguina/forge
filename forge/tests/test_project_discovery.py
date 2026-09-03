"""Coverage for `forge.project.discovery` and `forge.project.hdl_scan` —
what FORGE can work out about a repository it has never seen.

The behavioural line these tests defend is the plan's central rule: a fact
the source proves is discovered, a convention that resolves unambiguously is
inferred, and anything with two equally valid readings is reported as a
question rather than resolved by picking one. The last of those is the one
worth guarding hardest — a regression there does not fail loudly, it
generates a design that builds, simulates, and is wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.project.discovery import (
    AMBIGUOUS,
    KIND_CLOCK,
    KIND_CONNECTION,
    KIND_MODULE,
    UNSUPPORTED,
    ProjectDiscovery,
    _normalise_port,
)
from forge.project.hdl_scan import (
    is_active_low,
    resolve_instantiation_graph,
    scan_verilog_file,
    scan_vhdl_file,
    top_level_candidates,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"


@pytest.fixture(scope="module")
def pipeline():
    return ProjectDiscovery().scan(FIXTURE)


# ── hdl_scan ──────────────────────────────────────────────────────────────

def test_scans_every_module_in_a_multi_module_file(tmp_path: Path) -> None:
    """`hdl_parser` stops at the first module; a foreign repository routinely
    puts several in one file, and missing all but the first would silently
    drop most of the design."""
    source = tmp_path / "blocks.v"
    source.write_text(
        "module a(input clk, output [7:0] q); endmodule\n"
        "module b(input clk, input [7:0] d, output r); endmodule\n"
        "module c(input clk); b u_b(); endmodule\n"
    )
    units = scan_verilog_file(source)

    assert [u.name for u in units] == ["a", "b", "c"]
    assert units[0].ports["q"].width == 8
    assert units[0].ports["q"].direction == "out"


def test_instantiation_graph_drops_names_that_are_not_modules(tmp_path: Path) -> None:
    """The per-file regex over-collects by design (a task call looks like an
    instantiation); intersecting with real module names is what makes the
    graph trustworthy."""
    source = tmp_path / "top.v"
    source.write_text(
        "module leaf(input clk); endmodule\n"
        "module top(input clk);\n"
        "  leaf u_leaf(.clk(clk));\n"
        "  initial $display(\"x\");\n"
        "  always @(posedge clk) some_task(1);\n"
        "endmodule\n"
    )
    units = scan_verilog_file(source)

    assert resolve_instantiation_graph(units) == {"leaf": (), "top": ("leaf",)}
    assert top_level_candidates(units) == ["top"]


def test_vhdl_entities_and_ports(tmp_path: Path) -> None:
    source = tmp_path / "unit.vhd"
    source.write_text(
        "entity filter is\n"
        "  port (\n"
        "    clk : in std_logic;\n"
        "    rst_n : in std_logic;\n"
        "    data_in : in std_logic_vector(15 downto 0);\n"
        "    data_out : out std_logic_vector(15 downto 0)\n"
        "  );\n"
        "end entity;\n"
    )
    units = scan_vhdl_file(source)

    assert [u.name for u in units] == ["filter"]
    assert units[0].ports["data_in"].width == 16
    assert units[0].clock_ports == ["clk"]
    assert units[0].reset_ports == ["rst_n"]


@pytest.mark.parametrize(
    "name,expected",
    [("rst_n", True), ("resetn", True), ("aresetn", True), ("n_rst", True),
     ("rst", False), ("reset", False)],
)
def test_reset_active_level_read_from_the_name(name: str, expected: bool) -> None:
    assert is_active_low(name) is expected


def test_a_data_port_is_not_mistaken_for_a_clock(tmp_path: Path) -> None:
    """`_matches` is a convention check, not a substring search: a port called
    `block_count` must not read as a clock just because 'clock' shares
    letters with it."""
    source = tmp_path / "m.v"
    source.write_text("module m(input clk, input [7:0] block_count, output clocked_out);\nendmodule\n")
    unit = scan_verilog_file(source)[0]

    assert unit.clock_ports == ["clk"]


# ── port-name normalisation ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "name,expected",
    [("candidate_out", "candidate"), ("candidate_in", "candidate"),
     ("o_candidate", "candidate"), ("i_candidate", "candidate"),
     ("hit", "hit"), ("out", "out"), ("in", "in")],
)
def test_direction_affixes_are_stripped_without_emptying_the_name(
    name: str, expected: str,
) -> None:
    assert _normalise_port(name) == expected


# ── whole-project discovery ───────────────────────────────────────────────

def test_finds_every_module_and_keeps_the_testbench_out(pipeline) -> None:
    assert sorted(u.name for u in pipeline.units) == ["decimator", "packer", "shaper"]
    assert [u.name for u in pipeline.testbench_units] == ["tb_pipeline"]


def test_classifies_files_by_what_they_are_for(pipeline) -> None:
    assert len(pipeline.files_by_role("rtl")) == 3
    assert len(pipeline.files_by_role("testbench")) == 1
    assert len(pipeline.files_by_role("constraint")) == 1


def test_infers_the_single_clock_and_reset_with_its_level(pipeline) -> None:
    assert [c.name for c in pipeline.clocks] == ["clk"]
    assert [(r.name, r.active_low) for r in pipeline.resets] == [("rst_n", True)]


def test_infers_the_pipeline_from_matching_port_names_and_widths(pipeline) -> None:
    edges = {(c.producer, c.consumer) for c in pipeline.connections}

    assert ("decimator.decimated", "shaper.decimated") in edges
    assert ("shaper.shaped", "packer.shaped") in edges
    assert len(pipeline.connections) == 4


def test_unmatched_ends_of_the_pipeline_become_top_level_ports(pipeline) -> None:
    assert pipeline.external_inputs == [
        "decimator.sample_in", "decimator.sample_in_valid",
    ]
    assert pipeline.external_outputs == [
        "packer.packet_out", "packer.packet_out_valid",
    ]


def test_a_leaf_only_project_has_no_structural_top(pipeline) -> None:
    """Nothing instantiates anything here, so there is no existing top level
    to replace — all three modules are blocks to integrate."""
    assert pipeline.structural_top is None
    assert len(pipeline.managed_units) == 3


def test_two_valid_consumers_produce_a_question_not_a_connection(tmp_path: Path) -> None:
    """The plan's E231 case. Both consumers satisfy width and direction, so
    FORGE must wire neither and say why."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "classifier.v").write_text(
        "module classifier(input clk, output [63:0] candidate_out);\nendmodule\n")
    (rtl / "formatter.v").write_text(
        "module formatter(input clk, input [63:0] candidate_in);\nendmodule\n")
    (rtl / "sink.v").write_text(
        "module sink(input clk, input [63:0] candidate);\nendmodule\n")

    result = ProjectDiscovery().scan(tmp_path)
    questions = result.ambiguities_of(KIND_CONNECTION)

    assert result.connections == []
    assert len(questions) == 1
    assert questions[0].classification == AMBIGUOUS
    assert questions[0].subject == "classifier.candidate_out"
    assert set(questions[0].options) == {"formatter.candidate_in", "sink.candidate"}
    assert questions[0].action is not None
    assert questions[0].action.safe is False


def test_two_producers_for_one_consumer_are_a_question_not_two_drivers(
    tmp_path: Path,
) -> None:
    """Each producer's sole candidate is the same input, so each looks
    unambiguous on its own — wiring both would put two drivers on one net,
    a design that elaborates and is wrong."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "a.v").write_text("module a(input clk, output [31:0] payload);\nendmodule\n")
    (rtl / "b.v").write_text("module b(input clk, output [31:0] payload);\nendmodule\n")
    (rtl / "sink.v").write_text("module sink(input clk, input [31:0] payload);\nendmodule\n")

    result = ProjectDiscovery().scan(tmp_path)
    questions = result.ambiguities_of(KIND_CONNECTION)

    assert result.connections == []
    assert len(questions) == 1
    assert questions[0].subject == "sink.payload"
    assert set(questions[0].options) == {"a.payload", "b.payload"}
    assert questions[0].action.id == "resolve-ambiguous-producer"


def test_a_multi_clock_module_is_reported_unsupported_not_integrated(tmp_path: Path) -> None:
    """The plan's Phase D3 example, verbatim: the user must learn about the
    limitation during adoption, with the ways forward spelled out."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "ethernet_core.v").write_text(
        "module ethernet_core(input rx_clk, input tx_clk, input axi_clk,\n"
        "                    input [63:0] rx_data, output [63:0] tx_data);\nendmodule\n")

    result = ProjectDiscovery().scan(tmp_path)

    assert [u.name for u in result.managed_units] == []
    assert len(result.unsupported) == 1
    unsupported = result.unsupported[0]
    assert unsupported.classification == UNSUPPORTED
    assert unsupported.kind == KIND_MODULE
    assert "one functional clock domain" in unsupported.question
    assert len(unsupported.options) == 3


def test_several_clock_names_raise_a_clock_question(tmp_path: Path) -> None:
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "a.v").write_text("module a(input clk_40, output [7:0] q);\nendmodule\n")
    (rtl / "b.v").write_text("module b(input clk_360, input [7:0] d);\nendmodule\n")

    result = ProjectDiscovery().scan(tmp_path)
    questions = result.ambiguities_of(KIND_CLOCK)

    assert len(questions) == 1
    assert set(questions[0].options) == {"clk_40", "clk_360"}


def test_build_output_directories_are_not_mistaken_for_sources(tmp_path: Path) -> None:
    """A previously generated top level sitting in `build/` must not come
    back as one of the user's own modules on the next scan."""
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "core.v").write_text("module core(input clk);\nendmodule\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "algo_top.v").write_text("module algo_top(input clk);\nendmodule\n")

    result = ProjectDiscovery().scan(tmp_path)

    assert [u.name for u in result.units] == ["core"]


def test_discovery_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "a.v").write_text("module a(input clk);\nendmodule\n")
    before = sorted(p.name for p in tmp_path.rglob("*"))

    ProjectDiscovery().scan(tmp_path)

    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_scanning_a_file_rather_than_a_directory_is_a_user_input_error(tmp_path: Path) -> None:
    source = tmp_path / "a.v"
    source.write_text("module a(input clk);\nendmodule\n")

    with pytest.raises(NotADirectoryError):
        ProjectDiscovery().scan(source)
