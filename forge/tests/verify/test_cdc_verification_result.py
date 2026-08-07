"""Tests for forge.verification.cdc_verification_result. report_all_crossings'
own crossing-detection logic is tested in forge/tests/test_cdc.py
alongside verify_cdc; this file covers construction/serialization plus
build_cdc_verification_result's join.
"""
from __future__ import annotations

from pathlib import Path

from forge.contracts.config import Connection, DesignConfig, Module
from forge.contracts.contract_loader import LoadedContract
from forge.contracts.matcher import auto_match_ports
from forge.verification.cdc_verification_result import (
    CDC_VERIFICATION_RESULT_SCHEMA,
    CdcCrossingResult,
    CdcVerificationResult,
    build_cdc_verification_result,
    render_cdc_verification_markdown,
)


def test_schema_tag():
    assert CDC_VERIFICATION_RESULT_SCHEMA.name == "forge.cdc_verification_result"
    assert CDC_VERIFICATION_RESULT_SCHEMA.version == "1.0"


def test_crossing_result_to_dict_roundtrips_fields():
    c = CdcCrossingResult(
        connection="norm->thresh",
        kind="level_sync",
        source_clock_domain="clk_a",
        destination_clock_domain="clk_b",
        source_reset_domain="rst",
        destination_reset_domain="rst",
        property_checked="clock-domain crossing declared with an approved cdc: adapter",
        passed=True,
        message="approved via cdc: {kind: level_sync}",
    )
    d = c.to_dict()
    assert d["connection"] == "norm->thresh"
    assert d["kind"] == "level_sync"
    assert d["passed"] is True


def test_undeclared_crossing_result_is_not_passed():
    c = CdcCrossingResult(
        connection="src->dst",
        kind=None,
        source_clock_domain="clk_a",
        destination_clock_domain="clk_b",
        source_reset_domain=None,
        destination_reset_domain=None,
        property_checked="clock-domain crossing declared with an approved cdc: adapter",
        passed=False,
        message="undeclared clock-domain crossing: 'clk_a' -> 'clk_b'",
    )
    assert c.to_dict()["passed"] is False
    assert c.to_dict()["kind"] is None


def test_verification_result_to_dict_composes_crossings():
    result = CdcVerificationResult(
        schema=CDC_VERIFICATION_RESULT_SCHEMA,
        design_hash="abc123",
        crossings=[
            CdcCrossingResult(
                connection="norm->thresh", kind="level_sync",
                source_clock_domain="ap_clk", destination_clock_domain="ap_clk",
                source_reset_domain="ap_rst", destination_reset_domain="ap_rst",
                property_checked="clock-domain crossing declared with an approved cdc: adapter",
                passed=True, message="same domain, no cdc: needed",
            ),
        ],
    )
    d = result.to_dict()
    assert d["schema"] == {"name": "forge.cdc_verification_result", "version": "1.0"}
    assert d["design_hash"] == "abc123"
    assert len(d["crossings"]) == 1


def test_verification_result_defaults_crossings_to_empty():
    result = CdcVerificationResult(schema=CDC_VERIFICATION_RESULT_SCHEMA, design_hash="abc123")
    assert result.crossings == []


def _contract(module_name, clock_port, reset_port="rst"):
    spec = {
        "ip_interface": {
            "module_name": module_name, "ip_info_key": module_name, "source_type": "rtl",
            "roles": {
                "clock_primary": {"raw_port": clock_port, "direction": "input", "width": 1},
                "reset_primary": {"raw_port": reset_port, "direction": "input", "width": 1},
            },
        }
    }
    return LoadedContract(path=Path(f"/fake/{module_name}.interface.yaml"), spec=spec)


def _port(name, direction, width=8):
    return {"name": name, "direction": direction, "width": width, "type": "wire"}


def test_build_cdc_verification_result_wraps_report_all_crossings():
    # forge/tests/ is exempt from the forge.contracts/forge.generation<->forge.verification
    # cross-import rule (ci/import_direction_check.sh) — real production
    # code never imports both directly; the CLI layer composes them (see
    # forge/core/cli/groups/topgen.py's cmd_validate).
    from forge.contracts.cdc import report_all_crossings

    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")])],
    )
    ip_info = {
        "src": {"ports": [_port("clk_a", "IN", 1), _port("rst", "IN", 1), _port("dout", "OUT")]},
        "dst": {"ports": [_port("clk_b", "IN", 1), _port("rst", "IN", 1), _port("din", "IN")]},
    }
    contracts = {"src": _contract("src", "clk_a"), "dst": _contract("dst", "clk_b")}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)

    crossings = report_all_crossings(cfg, contracts, report, conn_map, global_nets)
    result = build_cdc_verification_result(crossings, "abc123")

    assert result.schema == CDC_VERIFICATION_RESULT_SCHEMA
    assert result.design_hash == "abc123"
    assert len(result.crossings) == 1
    assert result.crossings[0].passed is False


def test_render_cdc_verification_markdown_rejects_unrecognised_schema():
    md = render_cdc_verification_markdown({"schema": {"name": "not.cdc", "version": "1.0"}})
    assert "Unrecognised" in md


def test_render_cdc_verification_markdown_handles_no_crossings():
    result = CdcVerificationResult(schema=CDC_VERIFICATION_RESULT_SCHEMA, design_hash="abc123")
    md = render_cdc_verification_markdown(result.to_dict())
    assert "No clock/reset-domain crossings" in md


def test_render_cdc_verification_markdown_renders_failing_crossing():
    result = CdcVerificationResult(
        schema=CDC_VERIFICATION_RESULT_SCHEMA, design_hash="abc123",
        crossings=[CdcCrossingResult(
            connection="src->dst", kind=None,
            source_clock_domain="clk_a", destination_clock_domain="clk_b",
            source_reset_domain=None, destination_reset_domain=None,
            property_checked="clock/reset-domain crossing declared with an approved adapter",
            passed=False, message="undeclared crossing",
        )],
    )
    md = render_cdc_verification_markdown(result.to_dict())
    assert "src->dst" in md
    assert "1 (1 failing)" in md
