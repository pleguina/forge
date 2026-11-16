"""Tests for forge.core.utils.hdl_parser — VHDL/Verilog entity/port scanning
and the safe arithmetic evaluator used for generic/parameter-driven widths.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forge.core.utils.hdl_parser import (
    _eval_vhdl_expr,
    _eval_verilog_expr,
    _safe_eval_arith,
    _scan_ports,
    _scan_verilog_ports,
    _verilog_module_name,
    _vhdl_entity_name,
    _width_from_slice,
    PortDecl,
    UnresolvedWidthError,
    parse_verilog_defines,
    scan_module_ports,
    scan_verilog_port_decls,
    scan_vhdl_port_decls,
)


# ---------------------------------------------------------------------------
# Safe arithmetic evaluator
# ---------------------------------------------------------------------------

class TestSafeEvalArith:
    def test_plain_integer(self):
        assert _safe_eval_arith("15") == 15

    def test_subtraction(self):
        assert _safe_eval_arith("4-1") == 3

    def test_addition_with_spaces(self):
        assert _safe_eval_arith("4 + 2") == 6

    def test_parens_and_precedence(self):
        assert _safe_eval_arith("(4+2)*3") == 18

    def test_division_truncates_toward_zero_like_old_eval(self):
        # int(eval("10/3")) == 3; int(eval("-10/3")) == -3 (truncation, not floor)
        assert _safe_eval_arith("10/3") == 3
        assert _safe_eval_arith("-10/3") == -3

    def test_unary_minus(self):
        assert _safe_eval_arith("-5+2") == -3

    def test_rejects_non_arithmetic_syntax(self):
        with pytest.raises((SyntaxError, ValueError)):
            _safe_eval_arith("__import__('os')")

    def test_rejects_name_nodes(self):
        with pytest.raises(ValueError):
            _safe_eval_arith("N")  # bare identifiers aren't arithmetic constants


# ---------------------------------------------------------------------------
# Generic/parameter substitution wrappers
# ---------------------------------------------------------------------------

class TestEvalVhdlExpr:
    def test_plain_number(self):
        assert _eval_vhdl_expr("15", {}) == 15

    def test_generic_substitution(self):
        assert _eval_vhdl_expr("N-1", {"N": 8}) == 7

    def test_multiple_generics(self):
        assert _eval_vhdl_expr("WIDTH+OFFSET", {"WIDTH": 4, "OFFSET": 2}) == 6

    def test_unresolvable_expression_raises(self):
        with pytest.raises(ValueError):
            _eval_vhdl_expr("UNKNOWN_GENERIC", {})


class TestEvalVerilogExpr:
    def test_plain_number(self):
        assert _eval_verilog_expr("15", {}) == 15

    def test_param_substitution(self):
        assert _eval_verilog_expr("WIDTH-1", {"WIDTH": 32}) == 31

    def test_unresolvable_expression_raises(self):
        with pytest.raises(ValueError):
            _eval_verilog_expr("UNKNOWN_PARAM", {})

    def test_clog2_sized_literals_and_shifts(self):
        assert _eval_verilog_expr("$clog2(DEPTH)-1", {"DEPTH": 16}) == 3
        assert _eval_verilog_expr("$clog2(17)", {}) == 5
        assert _eval_verilog_expr("8'hF + 'd1", {}) == 16
        assert _eval_verilog_expr("1 << 3", {}) == 8
        assert _eval_verilog_expr("2**W", {"W": 4}) == 16

    def test_macros_come_from_defines(self):
        assert _eval_verilog_expr("`DW-1", {}, {"DW": "12"}) == 11
        with pytest.raises(ValueError):
            _eval_verilog_expr("`DW-1", {})

    def test_other_system_functions_are_refused(self):
        with pytest.raises(ValueError):
            _eval_verilog_expr("$bits(t)-1", {})


# ---------------------------------------------------------------------------
# _width_from_slice
# ---------------------------------------------------------------------------

class TestWidthFromSlice:
    def test_none_is_width_one(self):
        assert _width_from_slice(None) == 1

    def test_simple_numeric_range(self):
        assert _width_from_slice("[7:0]") == 8

    def test_single_bit_like_range(self):
        assert _width_from_slice("[0:0]") == 1

    def test_parameterized_range(self):
        assert _width_from_slice("[IN_W-1:0]", {"IN_W": 54}) == 54

    def test_malformed_slice_raises(self):
        with pytest.raises(ValueError):
            _width_from_slice("not a range")


# ---------------------------------------------------------------------------
# VHDL entity/port scanning
# ---------------------------------------------------------------------------

class TestVhdlScanning:
    def test_entity_name(self, tmp_path: Path):
        vhdl = tmp_path / "foo.vhd"
        vhdl.write_text("entity my_entity is\nend entity;\n")
        assert _vhdl_entity_name(vhdl) == "my_entity"

    def test_entity_name_fallback_when_absent(self, tmp_path: Path):
        vhdl = tmp_path / "foo.vhd"
        vhdl.write_text("-- no entity here\n")
        assert _vhdl_entity_name(vhdl, fallback="fallback_name") == "fallback_name"

    def test_scan_ports_simple(self, tmp_path: Path):
        vhdl = tmp_path / "foo.vhd"
        vhdl.write_text(textwrap.dedent("""\
            entity foo is
            port (
                clk   : in  std_logic;
                rst   : in  std_logic;
                data  : out std_logic_vector(7 downto 0)
            );
            end entity;
        """))
        ports = _scan_ports(vhdl)
        assert ports["clk"] == ("in", 1)
        assert ports["rst"] == ("in", 1)
        assert ports["data"] == ("out", 8)

    def test_scan_ports_with_generic_width(self, tmp_path: Path):
        vhdl = tmp_path / "foo.vhd"
        vhdl.write_text(textwrap.dedent("""\
            entity foo is
            generic (
                N : positive := 16
            );
            port (
                din : in std_logic_vector(N-1 downto 0)
            );
            end entity;
        """))
        ports = _scan_ports(vhdl)
        assert ports["din"] == ("in", 16)


# ---------------------------------------------------------------------------
# Verilog/SystemVerilog module/port scanning
# ---------------------------------------------------------------------------

class TestVerilogScanning:
    def test_module_name(self, tmp_path: Path):
        v = tmp_path / "foo.v"
        v.write_text("module my_mod (input clk);\nendmodule\n")
        assert _verilog_module_name(v) == "my_mod"

    def test_module_name_fallback_when_absent(self, tmp_path: Path):
        v = tmp_path / "foo.v"
        v.write_text("// no module here\n")
        assert _verilog_module_name(v, fallback="fallback_name") == "fallback_name"

    def test_ansi_style_ports(self, tmp_path: Path):
        v = tmp_path / "foo.v"
        v.write_text(textwrap.dedent("""\
            module foo (
                input        clk,
                input  [7:0] data_in,
                output       valid
            );
            endmodule
        """))
        ports = _scan_verilog_ports(v)
        assert ports["clk"] == ("in", 1)
        assert ports["data_in"] == ("in", 8)
        assert ports["valid"] == ("out", 1)

    def test_parameterized_width(self, tmp_path: Path):
        v = tmp_path / "foo.v"
        v.write_text(textwrap.dedent("""\
            module foo #(parameter IN_W = 54) (
                input [IN_W-1:0] din,
                output           dout
            );
            endmodule
        """))
        ports = _scan_verilog_ports(v)
        assert ports["din"] == ("in", 54)

    def test_non_ansi_style_ports(self, tmp_path: Path):
        v = tmp_path / "foo.v"
        v.write_text(textwrap.dedent("""\
            module foo (clk, data_in);
            input clk;
            input [7:0] data_in;
            endmodule
        """))
        ports = _scan_verilog_ports(v)
        assert ports["clk"] == ("in", 1)
        assert ports["data_in"] == ("in", 8)

    def test_strips_comments_before_parsing(self, tmp_path: Path):
        v = tmp_path / "foo.v"
        v.write_text(textwrap.dedent("""\
            /* block comment
               spanning lines */
            module foo ( // trailing comment
                input clk // another comment
            );
            endmodule
        """))
        ports = _scan_verilog_ports(v)
        assert ports["clk"] == ("in", 1)

def test_ansi_multiname_declaration_applies_to_every_name(tmp_path):
    """`output wire [W-1:0] a, b, c` declares three identical ports.

    The header blob is split on commas before being matched, so only the
    first name arrives carrying the direction and width; the rest are bare
    names. They used to fall through to a hardcoded ('in', 1) default,
    silently turning 8-bit outputs into 1-bit inputs. Found in
    vision_pipeline_demo's window_builder_rtl, where the corruption was
    invisible because the hand-written interface contract restated the
    correct direction and width for every tap.
    """
    f = tmp_path / "m.v"
    f.write_text(
        "module m #(parameter W = 8) (\n"
        "    input  wire              clk,\n"
        "    output wire [W-1:0]      a, b, c,\n"
        "    input  wire [3:0]        d, e,\n"
        "    output wire              f\n"
        ");\n"
        "endmodule\n"
    )

    ports = _scan_verilog_ports(f)

    assert ports["a"] == ("out", 8)
    assert ports["b"] == ("out", 8)
    assert ports["c"] == ("out", 8)
    assert ports["d"] == ("in", 4)
    assert ports["e"] == ("in", 4)
    assert ports["f"] == ("out", 1)
    assert ports["clk"] == ("in", 1)


def test_non_ansi_header_names_still_resolve_from_body_declarations(tmp_path):
    """The multi-name fix must not shadow the non-ANSI path, where a bare
    header name takes its direction/width from a body declaration rather
    than from the preceding header entry."""
    f = tmp_path / "n.v"
    f.write_text(
        "module n(a, b, c);\n"
        "  input  [7:0] a;\n"
        "  output       b;\n"
        "  inout  [1:0] c;\n"
        "endmodule\n"
    )

    ports = _scan_verilog_ports(f)

    assert ports["a"] == ("in", 8)
    assert ports["b"] == ("out", 1)
    assert ports["c"] == ("inout", 2)


def test_clog2_address_port_has_its_real_width(tmp_path):
    """`[$clog2(DEPTH)-1:0]` with DEPTH=16 is 4 bits. The evaluator used to
    give up and return 64, which adoption then published as a 65-bit port."""
    f = tmp_path / "lookup.v"
    f.write_text(
        "module lookup #(parameter DEPTH = 16) (\n"
        "    input  wire                     clk,\n"
        "    input  wire [$clog2(DEPTH)-1:0] lookup_addr,\n"
        "    output reg  [7:0]               lookup_data\n"
        ");\n"
        "endmodule\n"
    )

    assert _scan_verilog_ports(f)["lookup_addr"] == ("in", 4)


def test_parameters_may_be_expressions_of_earlier_ones(tmp_path):
    f = tmp_path / "m.v"
    f.write_text(
        "module m #(parameter A = 4, B = A*2, parameter integer C = 8'hF) (\n"
        "    input  [B-1:0] y,\n"
        "    output [C:0]   q,\n"
        "    output [L-1:0] r\n"
        ");\n"
        "  localparam L = $clog2(B) + 1;\n"
        "endmodule\n"
    )

    ports = _scan_verilog_ports(f)

    assert ports["y"] == ("in", 8)
    assert ports["q"] == ("out", 16)
    assert ports["r"] == ("out", 4)


def test_an_unevaluable_width_is_an_error_not_a_guess(tmp_path):
    f = tmp_path / "m.v"
    f.write_text("module m (input [`ADDR_W-1:0] a, input [W-1:0] b, input c);\nendmodule\n")

    with pytest.raises(UnresolvedWidthError) as excinfo:
        _scan_verilog_ports(f)

    assert excinfo.value.unresolved == {"a": "[`ADDR_W-1:0]", "b": "[W-1:0]"}
    decls = scan_verilog_port_decls(f.read_text())
    assert decls["a"].width is None and decls["c"] == PortDecl("in", 1)


def test_instance_parameters_and_defines_reach_the_widths(tmp_path):
    f = tmp_path / "m.v"
    f.write_text("module m #(parameter W = 8) (input [W-1:0] a, input [`DW-1:0] b);\nendmodule\n")

    ports = scan_module_ports(f, {"W": 32}, parse_verilog_defines(["DW=6", "FLAG"]))

    assert ports == {"a": ("in", 32), "b": ("in", 6)}


def test_declared_widths_fill_only_what_the_source_leaves_open(tmp_path):
    f = tmp_path / "m.v"
    f.write_text("module m (input [`ADDR_W-1:0] a, input [7:0] b);\nendmodule\n")

    assert scan_module_ports(f, declared_widths={"a": 4}) == {"a": ("in", 4), "b": ("in", 8)}
    with pytest.raises(ValueError, match="contradicts"):
        scan_module_ports(f, declared_widths={"a": 4, "b": 9})
    with pytest.raises(ValueError, match="unknown port"):
        scan_module_ports(f, declared_widths={"a": 4, "nope": 1})


def test_vhdl_unknown_constant_is_unresolved_and_generic_expressions_evaluate():
    decls = scan_vhdl_port_decls(
        "entity e is\n"
        "  generic ( N : integer := 8; -- comment\n"
        "            M : natural := N*2 );\n"
        "  port (\n"
        "    a : in  std_logic_vector(M-1 downto 0);\n"
        "    b : out unsigned(N-1 downto 0);\n"
        "    c : out std_logic_vector(PKG_W-1 downto 0)\n"
        "  );\n"
        "end entity;\n"
    )

    assert decls["a"].width == 16
    assert decls["b"].width == 8
    assert decls["c"].width is None


def test_declarations_are_read_whole():
    decls = scan_verilog_port_decls(
        "module m #(parameter N = 2) (\n"
        "  input  logic [N-1:0][7:0] packed2,\n"
        "  input[3:0] tight,\n"
        "  output int counter,\n"
        "  (* keep *) output logic signed [15:0] y = 0,\n"
        "  input  my_pkg::word_t w,\n"
        "  axi_if.slave bus,\n"
        "  input  [7:0] arr [4]\n"
        "); endmodule\n"
    )

    assert decls["packed2"] == PortDecl("in", 16)
    assert decls["tight"] == PortDecl("in", 4)
    assert decls["counter"] == PortDecl("out", 32)
    assert decls["y"] == PortDecl("out", 16)
    assert decls["w"] == PortDecl("in", None, "of type my_pkg::word_t")
    assert decls["bus"] == PortDecl("inout", None, "of interface type axi_if.slave")
    assert decls["arr"] == PortDecl("in", None, "with unpacked dimension [4]")


def test_non_ansi_declarations_on_one_line_and_undeclared_names():
    decls = scan_verilog_port_decls(
        "module n(a, b, c); input [1:0][3:0] a; (* x *) output reg [7:0] b; endmodule")

    assert decls["a"] == PortDecl("in", 8)
    assert decls["b"] == PortDecl("out", 8)
    assert decls["c"].width is None
