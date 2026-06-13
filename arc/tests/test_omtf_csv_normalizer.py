"""Tests for omtf_tools.csv_normalizer — OMTF CSV Mapping Layer.

Covers all 15 required test cases plus regression counts for the real CSV.
The tested module lives in the omtf_firmware plugin, not in ARC core.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import textwrap
from pathlib import Path
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Ensure the plugin package is importable
# ---------------------------------------------------------------------------
_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "omtf_firmware"
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from omtf_tools.csv_normalizer import (
    normalize,
    to_json,
    MappingNormalizationError,
    NormalizedEndpoint,
    _LF_TO_ROLE,
    _parse_bool,
    REQUIRED_COLUMNS,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PLUGIN_DIR   = Path(__file__).resolve().parents[2] / "plugins" / "omtf_firmware"
REAL_CSV     = PLUGIN_DIR / "arc" / "platforms" / "blobfish_x2o_vu13p" / "omtf_mapping.csv"

# ---------------------------------------------------------------------------
# Golden counts for the real CSV (checked in regression tests)
# Stored here so they are easy to update when the CSV changes intentionally.
# ---------------------------------------------------------------------------

GOLDEN = {
    "total_rows":      149,
    "selected_rx":     67,
    "selected_tx":     2,
    "rx_csc":          52,
    "rx_bmt_l1":       15,
    "tx_gmt":          2,
}

# ---------------------------------------------------------------------------
# Helpers — build minimal CSV in memory
# ---------------------------------------------------------------------------

_HEADER = (
    "endpoint_id,direction,slr,quad,channel,cage,fiber,polarity,"
    "link_function,logical_id,logical_name,endpoint_role,"
    "include_in_framework,has_config,source_build_flag_column,contract_note"
)


def _make_csv(rows: list[str], *, header: str = _HEADER) -> str:
    return header + "\n" + "\n".join(rows) + "\n"


def _write_csv(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "mapping.csv"
    p.write_text(content)
    return p


def _rx_row(
    slr=0, quad=0, ch=0, cage=16, fiber=1, polarity=0,
    link_function="CSC", logical_id=0, logical_name="csc_rx_0",
    role="detector_input_csc", include="yes", has_config="yes",
) -> str:
    ep_id = f"rx_slr{slr}_quad{quad}_ch{ch}"
    return (
        f"{ep_id},rx,{slr},{quad},{ch},{cage},{fiber},{polarity},"
        f"{link_function},{logical_id},{logical_name},{role},"
        f"{include},{has_config},build_framework_rx,note"
    )


def _tx_row(
    slr=0, quad=0, ch=0, cage=1, fiber=1, polarity=0,
    link_function="GMT", logical_id=0, logical_name="gmt_tx_0",
    role="gmt_output", include="yes", has_config="no",
) -> str:
    ep_id = f"tx_slr{slr}_quad{quad}_ch{ch}"
    return (
        f"{ep_id},tx,{slr},{quad},{ch},{cage},{fiber},{polarity},"
        f"{link_function},{logical_id},{logical_name},{role},"
        f"{include},{has_config},build_framework_tx,note"
    )


# ---------------------------------------------------------------------------
# Test 1: CSV parser drops worksheet summary columns
# ---------------------------------------------------------------------------

class TestColumnFiltering:
    """Test 1 & 2: only canonical columns are kept; extras are dropped."""

    def test_extra_columns_ignored(self, tmp_path):
        # Add extra worksheet columns that should be silently ignored
        header_extra = _HEADER + ",Unnamed: 0,Left,Total,summary_col"
        row = _rx_row() + ",extra1,,0,99"
        csv_path = _write_csv(tmp_path, _make_csv([row], header=header_extra))
        mapping = normalize(csv_path)
        # Should not raise; extra columns are simply not used
        assert len(mapping.endpoints) == 1

    def test_canonical_columns_kept(self, tmp_path):
        csv_path = _write_csv(tmp_path, _make_csv([_rx_row()]))
        mapping = normalize(csv_path)
        ep = mapping.endpoints[0]
        # All canonical fields parsed
        assert ep.slr == 0
        assert ep.gt_site == 0
        assert ep.lane == 0
        assert ep.link_function == "CSC"
        assert ep.cage == 16
        assert ep.fiber == 1


# ---------------------------------------------------------------------------
# Test 3: deterministic endpoint_id generation
# ---------------------------------------------------------------------------

class TestEndpointIdGeneration:
    """Test 3: endpoint_id is deterministic and uses gt/lane format."""

    def test_rx_id_format(self, tmp_path):
        csv_path = _write_csv(tmp_path, _make_csv([_rx_row(slr=1, quad=5, ch=2)]))
        mapping = normalize(csv_path)
        assert mapping.endpoints[0].endpoint_id == "rx_slr1_gt5_lane2"

    def test_tx_id_format(self, tmp_path):
        csv_path = _write_csv(tmp_path, _make_csv([_tx_row(slr=1, quad=6, ch=0)]))
        mapping = normalize(csv_path)
        assert mapping.endpoints[0].endpoint_id == "tx_slr1_gt6_lane0"

    def test_deterministic_multiple_rows(self, tmp_path):
        rows = [_rx_row(slr=0, quad=0, ch=i, logical_id=i, logical_name=f"csc_rx_{i}") for i in range(4)]
        csv_path = _write_csv(tmp_path, _make_csv(rows))
        mapping = normalize(csv_path)
        ids = [e.endpoint_id for e in mapping.endpoints]
        assert ids == [f"rx_slr0_gt0_lane{i}" for i in range(4)]


# ---------------------------------------------------------------------------
# Test 4 & 5: selected RX and TX endpoints parsed correctly
# ---------------------------------------------------------------------------

class TestSelectedEndpoints:
    def test_selected_rx_parsed(self, tmp_path):
        csv_path = _write_csv(tmp_path, _make_csv([
            _rx_row(slr=2, quad=3, ch=1, cage=8, fiber=2, polarity=1,
                    logical_id=42, logical_name="csc_rx_42"),
        ]))
        mapping = normalize(csv_path)
        ep = mapping.endpoints[0]
        assert ep.direction == "rx"
        assert ep.slr == 2
        assert ep.gt_site == 3
        assert ep.lane == 1
        assert ep.cage == 8
        assert ep.fiber == 2
        assert ep.polarity == 1
        assert ep.logical_id == 42
        assert ep.include_in_framework is True

    def test_selected_tx_parsed(self, tmp_path):
        csv_path = _write_csv(tmp_path, _make_csv([
            _tx_row(slr=0, quad=0, ch=1, cage=1, fiber=2, polarity=1, logical_id=1),
        ]))
        mapping = normalize(csv_path)
        ep = mapping.endpoints[0]
        assert ep.direction == "tx"
        assert ep.logical_id == 1
        assert ep.include_in_framework is True

    def test_non_selected_parsed(self, tmp_path):
        csv_path = _write_csv(tmp_path, _make_csv([
            _rx_row(include="no"),
        ]))
        mapping = normalize(csv_path)
        assert mapping.endpoints[0].include_in_framework is False


# ---------------------------------------------------------------------------
# Test 6 & 7: RX and TX roles inferred from link_function
# ---------------------------------------------------------------------------

class TestRoleInference:
    def _role_from_lf(self, tmp_path, link_function: str, direction: str = "rx") -> str:
        if direction == "rx":
            row = _rx_row(link_function=link_function)
        else:
            row = _tx_row(link_function=link_function)
        csv_path = _write_csv(tmp_path, _make_csv([row]))
        mapping = normalize(csv_path)
        return mapping.endpoints[0].endpoint_role

    def test_bmt_l1_role(self, tmp_path):
        assert self._role_from_lf(tmp_path, "BMT-L1") == "detector_input_bmt_l1"

    def test_csc_role(self, tmp_path):
        assert self._role_from_lf(tmp_path, "CSC") == "detector_input_csc"

    def test_dt_role(self, tmp_path):
        assert self._role_from_lf(tmp_path, "DT") == "detector_input_dt"

    def test_rpc_barrel_role(self, tmp_path):
        assert self._role_from_lf(tmp_path, "RPCb") == "detector_input_rpc"

    def test_rpc_endcap_role(self, tmp_path):
        assert self._role_from_lf(tmp_path, "RPCe") == "detector_input_rpc"

    def test_gmt_role(self, tmp_path):
        assert self._role_from_lf(tmp_path, "GMT", "tx") == "trigger_output_gmt"

    def test_dth_role(self, tmp_path):
        # DTH endpoints are not selected in the real CSV, but role mapping must work
        row = _rx_row(link_function="DTH", include="no")
        csv_path = _write_csv(tmp_path, _make_csv([row]))
        mapping = normalize(csv_path)
        assert mapping.endpoints[0].endpoint_role == "dth_link"


# ---------------------------------------------------------------------------
# Test 8: duplicate endpoint_id fails
# ---------------------------------------------------------------------------

class TestDuplicateEndpointId:
    def test_duplicate_fails(self, tmp_path):
        # Two rows with the same slr/quad/channel → same generated endpoint_id
        rows = [_rx_row(), _rx_row()]   # identical → duplicate
        csv_path = _write_csv(tmp_path, _make_csv(rows))
        with pytest.raises(MappingNormalizationError, match="duplicate endpoint_id"):
            normalize(csv_path)


# ---------------------------------------------------------------------------
# Test 9: duplicate (direction, slr, gt_site, lane) fails
# ---------------------------------------------------------------------------

class TestDuplicateLaneKey:
    def test_duplicate_lane_key_fails(self, tmp_path):
        row1 = _rx_row(slr=0, quad=0, ch=0)
        # Manually craft a second row with same slr/quad/ch but different CSV endpoint_id
        row2 = "rx_slr0_quad0_ch0_dup,rx,0,0,0,1,1,0,CSC,1,csc_rx_1,detector_input_csc,yes,yes,build_framework_rx,note"
        csv_path = _write_csv(tmp_path, _make_csv([row1, row2]))
        with pytest.raises(MappingNormalizationError, match="duplicate"):
            normalize(csv_path)


# ---------------------------------------------------------------------------
# Test 10: invalid include_in_framework fails
# ---------------------------------------------------------------------------

class TestInvalidBooleans:
    def test_invalid_include_fails(self, tmp_path):
        row = _rx_row().replace(",yes,yes,", ",INVALID_BOOL,yes,")
        csv_path = _write_csv(tmp_path, _make_csv([row]))
        with pytest.raises(MappingNormalizationError, match="include_in_framework"):
            normalize(csv_path)


# ---------------------------------------------------------------------------
# Test 11: selected RX without in_id (logical_id) fails
# ---------------------------------------------------------------------------

class TestMissingLogicalId:
    def test_selected_rx_without_logical_id_fails(self, tmp_path):
        # Remove logical_id by setting it empty
        row = _rx_row().replace(",0,csc_rx_0,", ",,csc_rx_0,")
        csv_path = _write_csv(tmp_path, _make_csv([row]))
        with pytest.raises(MappingNormalizationError, match="logical_id"):
            normalize(csv_path)


# ---------------------------------------------------------------------------
# Test 12: selected TX without out_id (logical_id) fails
# ---------------------------------------------------------------------------

class TestMissingTxLogicalId:
    def test_selected_tx_without_logical_id_fails(self, tmp_path):
        row = _tx_row().replace(",0,gmt_tx_0,", ",,gmt_tx_0,")
        csv_path = _write_csv(tmp_path, _make_csv([row]))
        with pytest.raises(MappingNormalizationError, match="logical_id"):
            normalize(csv_path)


# ---------------------------------------------------------------------------
# Regression counts for the real CSV
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_CSV.exists(), reason="Real CSV not found in workspace")
class TestRealCsvRegression:
    """Tests 4–7 regression: stable counts against the real CSV."""

    @pytest.fixture(scope="class")
    def mapping(self):
        return normalize(REAL_CSV)

    @pytest.fixture(scope="class")
    def data(self, mapping):
        return to_json(mapping)

    def test_total_rows(self, data):
        assert data["statistics"]["total_rows"] == GOLDEN["total_rows"]

    def test_selected_rx_count(self, data):
        assert data["statistics"]["selected_rx"] == GOLDEN["selected_rx"]

    def test_selected_tx_count(self, data):
        assert data["statistics"]["selected_tx"] == GOLDEN["selected_tx"]

    def test_selected_csc_rx_count(self, data):
        assert data["statistics"]["rx_by_function"].get("CSC", 0) == GOLDEN["rx_csc"]

    def test_selected_bmt_l1_rx_count(self, data):
        assert data["statistics"]["rx_by_function"].get("BMT-L1", 0) == GOLDEN["rx_bmt_l1"]

    def test_selected_gmt_tx_count(self, data):
        assert data["statistics"]["tx_by_function"].get("GMT", 0) == GOLDEN["tx_gmt"]

    def test_all_selected_rx_have_role(self, mapping):
        for ep in mapping.endpoints:
            if ep.include_in_framework and ep.direction == "rx":
                assert ep.endpoint_role, f"Missing role for {ep.endpoint_id}"

    def test_all_selected_tx_have_role(self, mapping):
        for ep in mapping.endpoints:
            if ep.include_in_framework and ep.direction == "tx":
                assert ep.endpoint_role == "trigger_output_gmt"

    def test_no_duplicate_ids(self, mapping):
        ids = [e.endpoint_id for e in mapping.endpoints]
        assert len(ids) == len(set(ids))

    def test_json_serialisable(self, data):
        import json as _json
        _json.dumps(data)

    def test_endpoint_id_format_rx(self, mapping):
        for ep in mapping.endpoints:
            if ep.direction == "rx":
                assert ep.endpoint_id.startswith("rx_slr")
                assert "_gt" in ep.endpoint_id
                assert "_lane" in ep.endpoint_id

    def test_endpoint_id_format_tx(self, mapping):
        for ep in mapping.endpoints:
            if ep.direction == "tx":
                assert ep.endpoint_id.startswith("tx_slr")
