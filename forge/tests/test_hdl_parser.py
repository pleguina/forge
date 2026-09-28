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

    def test_unresolvable_expression_returns_default(self):
        # Verilog width evaluation intentionally falls back to a safe
        # default (64) instead of raising, unlike the VHDL variant.
        assert _eval_verilog_expr("UNKNOWN_PARAM", {}) == 64


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

    def test_malformed_slice_falls_back_to_one(self):
        assert _width_from_slice("not a range") == 1


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
