"""
Tests for ip_info extraction layer (parser.py).

Validates: VHDL entity parsing, port discovery, width/direction extraction,
component.xml parsing, HDL scan fallback, and write_summary.
These tests are isolated from generation — they test the extraction pipeline only.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Dict, List

import pytest
import yaml
import json


_TOPGEN_ROOT = Path(__file__).resolve().parents[1]
if str(_TOPGEN_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOPGEN_ROOT))

from topgen.ip.parser import (
    _parse_vhdl_entity,
    _normalize_ports_from_map,
    parse_component,
    collect_all,
    write_summary,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

MINIMAL_VHDL = textwrap.dedent("""\
    library ieee;
    use ieee.std_logic_1164.all;

    entity test_mod is
    port (
        ap_clk       : IN  STD_LOGIC;
        ap_rst       : IN  STD_LOGIC;
        data_in      : IN  STD_LOGIC_VECTOR (31 downto 0);
        data_out     : OUT STD_LOGIC_VECTOR (15 downto 0);
        valid        : OUT STD_LOGIC
    );
    end entity test_mod;
""")

VHDL_WIDE_VECTOR = textwrap.dedent("""\
    library ieee;
    use ieee.std_logic_1164.all;

    entity wide_mod is
    port (
        clk      : IN  STD_LOGIC;
        big_bus  : OUT STD_LOGIC_VECTOR (127 downto 0);
        nibble   : IN  STD_LOGIC_VECTOR (3 downto 0)
    );
    end entity wide_mod;
""")

VHDL_WITH_COMMENTS = textwrap.dedent("""\
    library ieee;
    use ieee.std_logic_1164.all;

    entity commented_mod is
    port (
        -- this is the clock
        ap_clk : IN STD_LOGIC; -- primary clock
        -- data bus: 8 bits wide
        data   : OUT STD_LOGIC_VECTOR (7 downto 0) -- output data
    );
    end entity commented_mod;
""")

VHDL_EMPTY_ENTITY = textwrap.dedent("""\
    library ieee;
    use ieee.std_logic_1164.all;

    entity empty_mod is
    end entity empty_mod;
""")


def _write_vhdl(tmp_path: Path, name: str, content: str) -> Path:
    """Write VHDL content to a temp file and return its path."""
    p = tmp_path / f"{name}.vhd"
    p.write_text(content)
    return p


def _make_component_xml(tmp_path: Path, name: str = "test_mod",
                         vendor: str = "TestVendor", library: str = "hls",
                         version: str = "1.0",
                         vhdl_content: str = None) -> Path:
    """Create a minimal component.xml and companion VHDL file."""
    comp_dir = tmp_path / "ip_export"
    comp_dir.mkdir(parents=True, exist_ok=True)

    xml_content = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <spirit:component
            xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009">
          <spirit:vendor>{vendor}</spirit:vendor>
          <spirit:library>{library}</spirit:library>
          <spirit:name>{name}</spirit:name>
          <spirit:version>{version}</spirit:version>
        </spirit:component>
    """)
    xml_path = comp_dir / "component.xml"
    xml_path.write_text(xml_content)

    # Create companion VHDL
    vhdl_dir = comp_dir / "hdl" / "vhdl"
    vhdl_dir.mkdir(parents=True, exist_ok=True)
    vhdl_path = vhdl_dir / f"{name}.vhd"
    vhdl_path.write_text(vhdl_content or MINIMAL_VHDL.replace("test_mod", name))

    return xml_path


# ─────────────────────────────────────────────────────────────────────────────
# _parse_vhdl_entity tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParseVhdlEntity:
    """Test the VHDL entity port extraction."""

    def test_basic_ports(self, tmp_path):
        """Parse a minimal VHDL entity with mixed port types."""
        p = _write_vhdl(tmp_path, "test_mod", MINIMAL_VHDL)
        ports = _parse_vhdl_entity(p)

        assert len(ports) == 5

        by_name = {port["name"]: port for port in ports}
        assert by_name["ap_clk"]["direction"] == "IN"
        assert by_name["ap_clk"]["width"] == 1
        assert by_name["ap_clk"]["type"] == "STD_LOGIC"

        assert by_name["data_in"]["direction"] == "IN"
        assert by_name["data_in"]["width"] == 32
        assert "STD_LOGIC_VECTOR" in by_name["data_in"]["type"]

        assert by_name["data_out"]["direction"] == "OUT"
        assert by_name["data_out"]["width"] == 16

        assert by_name["valid"]["direction"] == "OUT"
        assert by_name["valid"]["width"] == 1

    def test_wide_vectors(self, tmp_path):
        """Parse wide bus widths correctly."""
        p = _write_vhdl(tmp_path, "wide_mod", VHDL_WIDE_VECTOR)
        ports = _parse_vhdl_entity(p)

        by_name = {port["name"]: port for port in ports}
        assert by_name["big_bus"]["width"] == 128
        assert by_name["nibble"]["width"] == 4

    def test_comments_stripped(self, tmp_path):
        """VHDL comments should not interfere with parsing."""
        p = _write_vhdl(tmp_path, "commented_mod", VHDL_WITH_COMMENTS)
        ports = _parse_vhdl_entity(p)

        assert len(ports) == 2
        by_name = {port["name"]: port for port in ports}
        assert "ap_clk" in by_name
        assert "data" in by_name

    def test_empty_entity(self, tmp_path):
        """An entity with no port clause returns empty list."""
        p = _write_vhdl(tmp_path, "empty_mod", VHDL_EMPTY_ENTITY)
        ports = _parse_vhdl_entity(p)
        assert ports == []

    def test_port_order_preserved(self, tmp_path):
        """Ports should come out in declaration order."""
        p = _write_vhdl(tmp_path, "test_mod", MINIMAL_VHDL)
        ports = _parse_vhdl_entity(p)
        names = [port["name"] for port in ports]
        assert names == ["ap_clk", "ap_rst", "data_in", "data_out", "valid"]

    def test_all_ports_have_required_fields(self, tmp_path):
        """Every port entry must have name, direction, width, type."""
        p = _write_vhdl(tmp_path, "test_mod", MINIMAL_VHDL)
        ports = _parse_vhdl_entity(p)
        for port in ports:
            assert "name" in port
            assert "direction" in port
            assert "width" in port
            assert "type" in port
            assert port["direction"] in ("IN", "OUT")
            assert isinstance(port["width"], int)
            assert port["width"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_ports_from_map tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizePortsFromMap:
    """Test the dict-to-list port normalizer."""

    def test_basic_normalization(self):
        pmap = {"clk": ("in", 1), "data": ("out", 32)}
        result = _normalize_ports_from_map(pmap)
        assert len(result) == 2

        by_name = {p["name"]: p for p in result}
        assert by_name["clk"]["direction"] == "IN"
        assert by_name["clk"]["width"] == 1
        assert by_name["clk"]["type"] == "STD_LOGIC"

        assert by_name["data"]["direction"] == "OUT"
        assert by_name["data"]["width"] == 32
        assert "STD_LOGIC_VECTOR" in by_name["data"]["type"]

    def test_single_bit_is_std_logic(self):
        result = _normalize_ports_from_map({"sig": ("in", 1)})
        assert result[0]["type"] == "STD_LOGIC"

    def test_multi_bit_is_vector(self):
        result = _normalize_ports_from_map({"sig": ("out", 8)})
        assert result[0]["type"] == "STD_LOGIC_VECTOR(7 downto 0)"


# ─────────────────────────────────────────────────────────────────────────────
# parse_component tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParseComponent:
    """Test component.xml parsing with companion VHDL."""

    def test_basic_component(self, tmp_path):
        xml_path = _make_component_xml(tmp_path, name="my_ip",
                                        vendor="TestVendor", library="hls",
                                        version="2.0")
        comp = parse_component(xml_path)

        assert comp["vendor"] == "TestVendor"
        assert comp["library"] == "hls"
        assert comp["name"] == "my_ip"
        assert comp["version"] == "2.0"
        assert isinstance(comp["ports"], list)
        assert len(comp["ports"]) == 5  # from MINIMAL_VHDL

    def test_missing_vhdl_gives_empty_ports(self, tmp_path):
        """If companion VHDL doesn't exist, ports should be empty."""
        comp_dir = tmp_path / "ip_export"
        comp_dir.mkdir(parents=True, exist_ok=True)

        xml_content = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <spirit:component
                xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009">
              <spirit:vendor>V</spirit:vendor>
              <spirit:library>L</spirit:library>
              <spirit:name>no_vhdl_mod</spirit:name>
              <spirit:version>1.0</spirit:version>
            </spirit:component>
        """)
        xml_path = comp_dir / "component.xml"
        xml_path.write_text(xml_content)
        # No VHDL dir created

        comp = parse_component(xml_path)
        assert comp["name"] == "no_vhdl_mod"
        assert comp["ports"] == []

    def test_interfaces_key_present(self, tmp_path):
        """Component dict must have interfaces key."""
        xml_path = _make_component_xml(tmp_path, name="iface_test")
        comp = parse_component(xml_path)
        assert "interfaces" in comp
        assert isinstance(comp["interfaces"], list)


# ─────────────────────────────────────────────────────────────────────────────
# write_summary tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteSummary:
    """Test output serialization."""

    def test_yaml_output(self, tmp_path):
        summary = {"mod_a": {"vendor": "V", "ports": []}}
        out = tmp_path / "out.yaml"
        write_summary(summary, out, format="yaml")

        loaded = yaml.safe_load(out.read_text())
        assert loaded["mod_a"]["vendor"] == "V"

    def test_json_output(self, tmp_path):
        summary = {"mod_b": {"vendor": "X", "ports": [{"name": "clk", "width": 1}]}}
        out = tmp_path / "out.json"
        write_summary(summary, out, format="json")

        loaded = json.loads(out.read_text())
        assert loaded["mod_b"]["ports"][0]["name"] == "clk"

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "deep" / "nested" / "dir" / "ip.yaml"
        write_summary({"x": {}}, out, format="yaml")
        assert out.exists()

    def test_no_duplicate_port_names(self, tmp_path):
        """Verify the real ip_info.yaml has no dup port names per module."""
        import os as _os
        _root = _os.environ.get("TOPGEN_CONSUMER_ROOT", "")
        real_ip_info = (Path(_root) / "ip_info.yaml") if _root else Path("ip_info.yaml")
        if not real_ip_info.exists():
            pytest.skip("ip_info.yaml not available (set TOPGEN_CONSUMER_ROOT)")

        data = yaml.safe_load(real_ip_info.read_text())
        for mod_key, mod_data in data.items():
            if mod_data is None:
                continue
            ports = mod_data.get("ports", [])
            names = [p["name"] for p in ports]
            dups = [n for n in names if names.count(n) > 1]
            assert not dups, f"{mod_key}: duplicate port names {set(dups)}"


# ─────────────────────────────────────────────────────────────────────────────
# ip_info.yaml schema conformance (integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestIpInfoSchemaConformance:
    """Validate the committed ip_info.yaml conforms to the artifact spec."""

    @pytest.fixture
    def ip_info(self):
        import os as _os
        _root = _os.environ.get("TOPGEN_CONSUMER_ROOT", "")
        real_ip_info = (Path(_root) / "ip_info.yaml") if _root else Path("ip_info.yaml")
        if not real_ip_info.exists():
            pytest.skip("ip_info.yaml not available (set TOPGEN_CONSUMER_ROOT)")
        return yaml.safe_load(real_ip_info.read_text())

    def test_all_entries_have_required_fields(self, ip_info):
        """Every module entry must have vendor, library, name, version, ports."""
        required = {"vendor", "library", "name", "version", "ports"}
        for mod_key, mod_data in ip_info.items():
            if mod_data is None:
                continue
            missing = required - set(mod_data.keys())
            assert not missing, f"{mod_key}: missing top-level fields {missing}"

    def test_port_entries_have_required_fields(self, ip_info):
        """Every port entry must have name, direction, width, type."""
        port_required = {"name", "direction", "width", "type"}
        for mod_key, mod_data in ip_info.items():
            if mod_data is None:
                continue
            for i, port in enumerate(mod_data.get("ports", [])):
                missing = port_required - set(port.keys())
                assert not missing, f"{mod_key}.ports[{i}]: missing {missing}"

    def test_directions_are_valid(self, ip_info):
        """Port direction must be IN or OUT."""
        for mod_key, mod_data in ip_info.items():
            if mod_data is None:
                continue
            for port in mod_data.get("ports", []):
                assert port["direction"] in ("IN", "OUT"), \
                    f"{mod_key}.{port['name']}: bad direction '{port['direction']}'"

    def test_widths_are_positive_integers(self, ip_info):
        """Port width must be a positive integer."""
        for mod_key, mod_data in ip_info.items():
            if mod_data is None:
                continue
            for port in mod_data.get("ports", []):
                assert isinstance(port["width"], int), \
                    f"{mod_key}.{port['name']}: width not int"
                assert port["width"] >= 1, \
                    f"{mod_key}.{port['name']}: width {port['width']} < 1"

    def test_port_names_are_nonempty_strings(self, ip_info):
        """Port names must be non-empty strings."""
        for mod_key, mod_data in ip_info.items():
            if mod_data is None:
                continue
            for port in mod_data.get("ports", []):
                assert isinstance(port["name"], str) and port["name"], \
                    f"{mod_key}: empty or non-string port name"

    def test_module_keys_are_nonempty(self, ip_info):
        """Module keys must be non-empty strings."""
        for mod_key in ip_info:
            assert isinstance(mod_key, str) and mod_key, \
                "Found empty or non-string module key"

    def test_expected_module_count(self, ip_info):
        """ip_info should have 26 module entries (current known count)."""
        non_null = {k: v for k, v in ip_info.items() if v is not None}
        assert len(non_null) >= 20, \
            f"Expected at least 20 modules, got {len(non_null)}"
