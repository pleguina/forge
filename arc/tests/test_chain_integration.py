"""
test_chain_integration.py — Full pipeline integration test for the OMTF
blobfish_x2o_vu13p build chain.

Tests the real chain:
  01  generate_payload_abi.py        → payload_abi.json (178 ports, 19 RX + 1 TX)
  02  generate_omtf_payload_endpoints.py → payload_endpoints.csv (149 rows)
  03  generate_payload_endpoints.py  → payload_endpoints.json (69 selected)
  04  arc framework import           → framework_import.json
  05  omtf_tools mapping normalize   → omtf_mapping.normalized.json
  06  omtf_tools mapping validate-fw → PASSED (276 checks)
  07  omtf_tools io generate         → detector_io.generated.yml
  08  omtf_tools check-gmt-output    → NEEDS_MORE_TX_LINKS (known mismatch)
  09  arc framework io-resolve       → detector_io.resolved.json
  10  arc framework emit-payload     → payload.v (1043+ lines)

Uses local checked-out repos — no network access required.
Requires:
  - arc (arc-framework/arc/) on sys.path or installed
  - omtf_tools on sys.path (PYTHONPATH=plugins/omtf_firmware/)
  - blobfish-fw-omtf present at ../blobfish-fw-omtf relative to arc-framework

Skip marker: @pytest.mark.integration
Run with: pytest arc/tests/test_chain_integration.py -v -m integration
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest
import yaml

# ---------------------------------------------------------------------------
# Root path constants
# ---------------------------------------------------------------------------

_ARC_ROOT  = Path(__file__).resolve().parents[2]   # arc-framework/
_OMTF_ROOT = _ARC_ROOT / "plugins" / "omtf_firmware"
_BF_ROOT   = _ARC_ROOT.parent.parent / "blobfish-fw-omtf"

# Source files
_OMTF_MAPPING_CSV = _OMTF_ROOT / "arc" / "platforms" / "blobfish_x2o_vu13p" / "omtf_mapping.csv"
_PROJECT_YAML     = _BF_ROOT / "examples" / "omtf_x2o_vu13p" / "project.yaml"
_DESIGN_YML       = _OMTF_ROOT / "arc" / "designs" / "design.yml"
_CANONICAL_DIO    = _OMTF_ROOT / "arc" / "detector_io.yml"

# Tool scripts
_GEN_ABI      = _BF_ROOT / "register_contract" / "tools" / "generate_payload_abi.py"
_GEN_OMTF_EP  = _BF_ROOT / "register_contract" / "tools" / "generate_omtf_payload_endpoints.py"
_GEN_EP_JSON  = _BF_ROOT / "register_contract" / "tools" / "generate_payload_endpoints.py"

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

def _blobfish_available() -> bool:
    return _BF_ROOT.exists() and _PROJECT_YAML.exists()


def _omtf_mapping_available() -> bool:
    return _OMTF_MAPPING_CSV.exists()


skip_no_blobfish = pytest.mark.skipif(
    not _blobfish_available(),
    reason=f"blobfish-fw-omtf not found at {_BF_ROOT}",
)
skip_no_mapping = pytest.mark.skipif(
    not _omtf_mapping_available(),
    reason=f"omtf_mapping.csv not found at {_OMTF_MAPPING_CSV}",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd, cwd=None, env_extra=None):
    """Run a command and return CompletedProcess.  Raises on non-zero exit."""
    env = {**__import__("os").environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )


def _python(*args, cwd=None, env_extra=None):
    return _run([sys.executable] + list(args), cwd=cwd, env_extra=env_extra)


def _arc(*args, cwd=None):
    return _python("-m", "arc", *args, cwd=cwd or _ARC_ROOT)


def _omtf_tools(*args, cwd=None):
    return _python(
        "-m", "omtf_tools", *args,
        cwd=cwd or _OMTF_ROOT,
        env_extra={"PYTHONPATH": str(_OMTF_ROOT)},
    )


# ---------------------------------------------------------------------------
# Fixture: build_chain
# A session-scoped fixture that runs all chain steps once and returns the
# output directory.  All test cases read from it.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def chain_outputs(tmp_path_factory):
    """Run the full OMTF generate chain in a tmp dir; return the output paths."""
    out = tmp_path_factory.mktemp("chain")

    paths = {
        "out":           out,
        "abi_json":      out / "01_abi" / "payload_abi.json",
        "ep_csv":        out / "01_abi" / "payload_endpoints.csv",
        "ep_json":       out / "01_abi" / "payload_endpoints.json",
        "mapping_json":  out / "02_mapping" / "omtf_mapping.normalized.json",
        "fw_dir":        out / "03_fw",
        "fw_json":       out / "03_fw" / "framework_import.json",
        "fw_check_md":   out / "03_fw" / "fw_check.md",
        "dio_yml":       out / "04_dio" / "detector_io.generated.yml",
        "gmt_md":        out / "05_gmt" / "gmt_report.md",
        "resolved_dir":  out / "04_dio",
        "resolved_json": out / "04_dio" / "detector_io.resolved.json",
        "payload_v":     out / "06_hdl" / "payload.v",
    }

    for d in {p.parent for p in paths.values() if p != out}:
        d.mkdir(parents=True, exist_ok=True)

    errors = {}

    # -----------------------------------------------------------------------
    # Step 01a: generate_payload_abi.py
    # -----------------------------------------------------------------------
    if _blobfish_available():
        result = _python(str(_GEN_ABI),
                         "--project-yaml", str(_PROJECT_YAML),
                         "--output", str(paths["abi_json"]))
        if result.returncode != 0:
            errors["step_01a_gen_abi"] = result.stderr
    else:
        errors["step_01a_gen_abi"] = "blobfish repo not available"

    # -----------------------------------------------------------------------
    # Step 01b: generate_omtf_payload_endpoints.py
    # -----------------------------------------------------------------------
    if _omtf_mapping_available() and "step_01a_gen_abi" not in errors:
        result = _python(str(_GEN_OMTF_EP),
                         "--omtf-csv", str(_OMTF_MAPPING_CSV),
                         "--output",   str(paths["ep_csv"]))
        if result.returncode != 0:
            errors["step_01b_gen_ep_csv"] = result.stderr
    else:
        errors.setdefault("step_01b_gen_ep_csv", "prerequisite failed")

    # -----------------------------------------------------------------------
    # Step 01c: generate_payload_endpoints.py → JSON
    # -----------------------------------------------------------------------
    if "step_01b_gen_ep_csv" not in errors and "step_01a_gen_abi" not in errors:
        result = _python(str(_GEN_EP_JSON),
                         "--csv",    str(paths["ep_csv"]),
                         "--abi",    str(paths["abi_json"]),
                         "--output", str(paths["ep_json"].parent))
        if result.returncode != 0:
            errors["step_01c_gen_ep_json"] = result.stderr
        else:
            # Script writes to a subdir: out/01_abi/payload_endpoints.json/payload_endpoints.json
            generated = paths["ep_json"].parent / "payload_endpoints.json" / "payload_endpoints.json"
            if generated.exists():
                shutil.copy2(generated, paths["ep_json"])

    # -----------------------------------------------------------------------
    # Step 02: mapping normalize
    # -----------------------------------------------------------------------
    if _omtf_mapping_available():
        result = _omtf_tools(
            "mapping", "normalize",
            "--csv", str(_OMTF_MAPPING_CSV),
            "--out", str(paths["mapping_json"]),
        )
        if result.returncode != 0:
            errors["step_02_normalize"] = result.stderr
    else:
        errors["step_02_normalize"] = "omtf_mapping.csv not available"

    # -----------------------------------------------------------------------
    # Step 03: arc framework import
    # -----------------------------------------------------------------------
    if "step_01c_gen_ep_json" not in errors and "step_01a_gen_abi" not in errors:
        result = _arc(
            "framework", "import",
            "--provider",  "blobfish",
            "--abi",       str(paths["abi_json"]),
            "--endpoints", str(paths["ep_json"]),
            "--out",       str(paths["fw_dir"]),
        )
        if result.returncode != 0:
            errors["step_03_fw_import"] = result.stderr

    # -----------------------------------------------------------------------
    # Step 04: validate-fw
    # -----------------------------------------------------------------------
    if "step_02_normalize" not in errors and "step_01a_gen_abi" not in errors:
        result = _omtf_tools(
            "mapping", "validate-fw",
            "--mapping",   str(paths["mapping_json"]),
            "--framework", str(paths["out"] / "01_abi"),
            "--out",       str(paths["fw_check_md"]),
        )
        if result.returncode not in (0,):
            errors["step_04_validate_fw"] = result.stdout + result.stderr

    # -----------------------------------------------------------------------
    # Step 05: detector_io generate
    # -----------------------------------------------------------------------
    if "step_02_normalize" not in errors:
        result = _omtf_tools(
            "io", "generate",
            "--mapping", str(paths["mapping_json"]),
            "--out",     str(paths["dio_yml"]),
        )
        if result.returncode != 0:
            errors["step_05_dio"] = result.stderr

    # -----------------------------------------------------------------------
    # Step 06: gmt check  (expected: NEEDS_MORE_TX_LINKS, exit 2)
    # -----------------------------------------------------------------------
    if "step_02_normalize" not in errors and _DESIGN_YML.exists():
        paths["gmt_md"].parent.mkdir(parents=True, exist_ok=True)
        result = _omtf_tools(
            "check-gmt-output",
            "--design",  str(_DESIGN_YML),
            "--mapping", str(paths["mapping_json"]),
            "--out",     str(paths["gmt_md"]),
        )
        paths["gmt_verdict"] = _extract_gmt_verdict(result.stdout)
        # exit 2 = NEEDS_MORE_TX_LINKS — not a tool failure
        if result.returncode not in (0, 2):
            errors["step_06_gmt"] = result.stderr

    # -----------------------------------------------------------------------
    # Step 07: io-resolve
    # -----------------------------------------------------------------------
    if ("step_03_fw_import" not in errors and "step_05_dio" not in errors
            and paths["fw_json"].exists() and paths["dio_yml"].exists()):
        result = _arc(
            "framework", "io-resolve",
            "--framework",   str(paths["fw_dir"]),
            "--detector-io", str(paths["dio_yml"]),
            "--out",         str(paths["resolved_dir"]),
        )
        if result.returncode != 0:
            errors["step_07_io_resolve"] = result.stderr

    # -----------------------------------------------------------------------
    # Step 08: emit-payload
    # -----------------------------------------------------------------------
    if "step_07_io_resolve" not in errors:
        result = _arc(
            "framework", "emit-payload",
            "--framework",   str(paths["fw_dir"]),
            "--detector-io", str(paths["resolved_json"]),
            "--algo-module", "algo_top",
            "--out",         str(paths["payload_v"]),
        )
        if result.returncode != 0:
            errors["step_08_emit_payload"] = result.stderr

    paths["errors"] = errors
    return paths


def _extract_gmt_verdict(stdout: str) -> str:
    for v in ("OK", "NEEDS_MORE_TX_LINKS", "NEEDS_PACKER", "NEEDS_MAPPING_UPDATE"):
        if v in stdout:
            return v
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Individual step tests
# ---------------------------------------------------------------------------

@skip_no_blobfish
class TestStep01GenerateAbi:
    def test_abi_file_created(self, chain_outputs):
        assert "step_01a_gen_abi" not in chain_outputs["errors"], \
            chain_outputs["errors"].get("step_01a_gen_abi")
        assert chain_outputs["abi_json"].exists()

    def test_abi_has_correct_board(self, chain_outputs):
        if not chain_outputs["abi_json"].exists():
            pytest.skip("ABI not generated")
        abi = json.loads(chain_outputs["abi_json"].read_text())
        assert abi.get("board") == "X2O_VU13P"

    def test_abi_has_omtf_rx_ports(self, chain_outputs):
        if not chain_outputs["abi_json"].exists():
            pytest.skip("ABI not generated")
        abi = json.loads(chain_outputs["abi_json"].read_text())
        ports = abi.get("ports", [])
        rx = [p for p in ports if p.get("kind") == "gt_rx_tdata"]
        assert len(rx) >= 19, f"Expected ≥19 RX data ports, got {len(rx)}"

    def test_abi_has_tx_port(self, chain_outputs):
        if not chain_outputs["abi_json"].exists():
            pytest.skip("ABI not generated")
        abi = json.loads(chain_outputs["abi_json"].read_text())
        ports = abi.get("ports", [])
        tx = [p for p in ports if p.get("kind") == "gt_tx_tdata"]
        assert len(tx) >= 1

    def test_ep_json_has_expected_count(self, chain_outputs):
        if "step_01c_gen_ep_json" in chain_outputs["errors"]:
            pytest.skip("Endpoint JSON not generated")
        ep = json.loads(chain_outputs["ep_json"].read_text())
        eps = ep.get("endpoints", [])
        sel = [e for e in eps if e.get("include_in_framework")]
        # 67 RX + 2 TX = 69 selected
        assert len(sel) == 69, f"Expected 69 selected endpoints, got {len(sel)}"
        rx_sel = [e for e in sel if e.get("direction") == "rx"]
        tx_sel = [e for e in sel if e.get("direction") == "tx"]
        assert len(rx_sel) == 67
        assert len(tx_sel) == 2


@skip_no_mapping
class TestStep02MappingNormalize:
    def test_normalized_json_created(self, chain_outputs):
        assert "step_02_normalize" not in chain_outputs["errors"], \
            chain_outputs["errors"].get("step_02_normalize")
        assert chain_outputs["mapping_json"].exists()

    def test_normalized_has_endpoints(self, chain_outputs):
        if not chain_outputs["mapping_json"].exists():
            pytest.skip("Normalized mapping not generated")
        data = json.loads(chain_outputs["mapping_json"].read_text())
        eps = data.get("endpoints", [])
        assert len(eps) > 0

    def test_normalized_has_149_rows(self, chain_outputs):
        if not chain_outputs["mapping_json"].exists():
            pytest.skip("Normalized mapping not generated")
        data = json.loads(chain_outputs["mapping_json"].read_text())
        assert len(data.get("endpoints", [])) == 149

    def test_normalized_has_67_rx_selected(self, chain_outputs):
        if not chain_outputs["mapping_json"].exists():
            pytest.skip()
        data = json.loads(chain_outputs["mapping_json"].read_text())
        rx_sel = [e for e in data.get("endpoints", [])
                  if e.get("include_in_framework") and e.get("direction") == "rx"]
        assert len(rx_sel) == 67

    def test_normalized_has_2_tx_selected(self, chain_outputs):
        if not chain_outputs["mapping_json"].exists():
            pytest.skip()
        data = json.loads(chain_outputs["mapping_json"].read_text())
        tx_sel = [e for e in data.get("endpoints", [])
                  if e.get("include_in_framework") and e.get("direction") == "tx"]
        assert len(tx_sel) == 2


@skip_no_blobfish
@skip_no_mapping
class TestStep03FrameworkImport:
    def test_framework_import_succeeded(self, chain_outputs):
        assert "step_03_fw_import" not in chain_outputs["errors"], \
            chain_outputs["errors"].get("step_03_fw_import")

    def test_framework_import_json_created(self, chain_outputs):
        if "step_03_fw_import" in chain_outputs["errors"]:
            pytest.skip()
        assert chain_outputs["fw_json"].exists()

    def test_framework_import_provider(self, chain_outputs):
        if not chain_outputs["fw_json"].exists():
            pytest.skip()
        fi = json.loads(chain_outputs["fw_json"].read_text())
        assert fi.get("provider") == "blobfish"

    def test_framework_import_board(self, chain_outputs):
        if not chain_outputs["fw_json"].exists():
            pytest.skip()
        fi = json.loads(chain_outputs["fw_json"].read_text())
        assert fi.get("board") == "X2O_VU13P"

    def test_framework_has_abi_index(self, chain_outputs):
        if not chain_outputs["fw_json"].exists():
            pytest.skip()
        fi = json.loads(chain_outputs["fw_json"].read_text())
        # rx_endpoints lists all RX ports from the ABI
        rx = fi.get("rx_endpoints", [])
        assert len(rx) > 0, "Framework import must have rx_endpoints"

    def test_validate_fw_passes(self, chain_outputs):
        """validate-fw must report PASSED with 0 failures against the real ABI."""
        assert "step_04_validate_fw" not in chain_outputs["errors"], \
            chain_outputs["errors"].get("step_04_validate_fw")

    def test_validate_fw_report_written(self, chain_outputs):
        if "step_04_validate_fw" in chain_outputs["errors"]:
            pytest.skip()
        assert chain_outputs["fw_check_md"].exists()


@skip_no_mapping
class TestStep05DetectorIo:
    def test_detector_io_created(self, chain_outputs):
        assert "step_05_dio" not in chain_outputs["errors"], \
            chain_outputs["errors"].get("step_05_dio")
        assert chain_outputs["dio_yml"].exists()

    def test_detector_io_has_67_inputs(self, chain_outputs):
        if not chain_outputs["dio_yml"].exists():
            pytest.skip()
        data = yaml.safe_load(chain_outputs["dio_yml"].read_text())
        inputs = data.get("detector_inputs", [])
        assert len(inputs) == 67, f"Expected 67 detector inputs, got {len(inputs)}"

    def test_detector_io_has_2_outputs(self, chain_outputs):
        if not chain_outputs["dio_yml"].exists():
            pytest.skip()
        data = yaml.safe_load(chain_outputs["dio_yml"].read_text())
        outputs = data.get("trigger_outputs", [])
        assert len(outputs) == 2, f"Expected 2 trigger outputs, got {len(outputs)}"

    def test_detector_io_uses_blobfish_endpoint_key(self, chain_outputs):
        """Generated detector_io.yml must use 'blobfish_endpoint' not 'endpoint_id'."""
        if not chain_outputs["dio_yml"].exists():
            pytest.skip()
        text = chain_outputs["dio_yml"].read_text()
        assert "blobfish_endpoint:" in text
        # Must NOT use the old 'endpoint_id' key in data entries
        lines_with_endpoint = [l for l in text.splitlines()
                                if "endpoint_id:" in l and not l.startswith("#")]
        assert not lines_with_endpoint, \
            "detector_io.yml should not use 'endpoint_id' — use 'blobfish_endpoint'"


class TestStep06GmtCheck:
    def test_gmt_verdict_known(self, chain_outputs):
        """GMT check must complete with a known verdict."""
        if "step_06_gmt" in chain_outputs["errors"]:
            pytest.fail(chain_outputs["errors"]["step_06_gmt"])
        assert chain_outputs.get("gmt_verdict") in (
            "OK", "NEEDS_MORE_TX_LINKS", "NEEDS_PACKER", "NEEDS_MAPPING_UPDATE"
        )

    def test_gmt_verdict_is_needs_more_tx(self, chain_outputs):
        """Known regression: 2 TX links, 3 algo GMT outputs → NEEDS_MORE_TX_LINKS."""
        if "step_06_gmt" in chain_outputs["errors"]:
            pytest.skip()
        assert chain_outputs.get("gmt_verdict") == "NEEDS_MORE_TX_LINKS", (
            f"Expected NEEDS_MORE_TX_LINKS, got {chain_outputs.get('gmt_verdict')!r}"
        )

    def test_gmt_report_written(self, chain_outputs):
        if "step_06_gmt" in chain_outputs["errors"]:
            pytest.skip()
        assert chain_outputs["gmt_md"].exists()


@skip_no_blobfish
@skip_no_mapping
class TestStep07IoResolve:
    def test_io_resolve_succeeded(self, chain_outputs):
        assert "step_07_io_resolve" not in chain_outputs["errors"], \
            chain_outputs["errors"].get("step_07_io_resolve")

    def test_resolved_json_created(self, chain_outputs):
        if "step_07_io_resolve" in chain_outputs["errors"]:
            pytest.skip()
        assert chain_outputs["resolved_json"].exists()

    def test_resolved_has_67_inputs(self, chain_outputs):
        if not chain_outputs["resolved_json"].exists():
            pytest.skip()
        data = json.loads(chain_outputs["resolved_json"].read_text())
        resolved = data.get("detector_inputs", [])
        assert len(resolved) == 67

    def test_resolved_has_2_outputs(self, chain_outputs):
        if not chain_outputs["resolved_json"].exists():
            pytest.skip()
        data = json.loads(chain_outputs["resolved_json"].read_text())
        resolved = data.get("trigger_outputs", [])
        assert len(resolved) == 2


@skip_no_blobfish
@skip_no_mapping
class TestStep08EmitPayload:
    def test_emit_payload_succeeded(self, chain_outputs):
        assert "step_08_emit_payload" not in chain_outputs["errors"], \
            chain_outputs["errors"].get("step_08_emit_payload")

    def test_payload_v_created(self, chain_outputs):
        if "step_08_emit_payload" in chain_outputs["errors"]:
            pytest.skip()
        assert chain_outputs["payload_v"].exists()

    def test_payload_v_is_nonempty(self, chain_outputs):
        if not chain_outputs["payload_v"].exists():
            pytest.skip()
        size = chain_outputs["payload_v"].stat().st_size
        assert size > 100, f"payload.v too small: {size} bytes"

    def test_payload_v_has_module_declaration(self, chain_outputs):
        if not chain_outputs["payload_v"].exists():
            pytest.skip()
        text = chain_outputs["payload_v"].read_text()
        assert "module " in text, "payload.v must contain a Verilog module declaration"

    def test_payload_v_instantiates_algo_top(self, chain_outputs):
        if not chain_outputs["payload_v"].exists():
            pytest.skip()
        text = chain_outputs["payload_v"].read_text()
        assert "algo_top" in text, "payload.v must instantiate the algo_top module"

    def test_payload_v_line_count_reasonable(self, chain_outputs):
        if not chain_outputs["payload_v"].exists():
            pytest.skip()
        lines = chain_outputs["payload_v"].read_text().count("\n")
        assert lines >= 500, f"payload.v seems too short ({lines} lines)"


# ---------------------------------------------------------------------------
# Chain-level regression tests
# ---------------------------------------------------------------------------

class TestChainRegressions:
    def test_no_step_errors(self, chain_outputs):
        """No step should fail.  This gives a quick overall PASS/FAIL."""
        errors = chain_outputs.get("errors", {})
        # GMT mismatch is a KNOWN issue, not a failure of the toolchain
        known_warnings = {"step_06_gmt"}
        real_errors = {k: v for k, v in errors.items() if k not in known_warnings}
        assert not real_errors, (
            "Chain step(s) failed:\n" +
            "\n".join(f"  {k}: {v[:200]}" for k, v in real_errors.items())
        )

    def test_selected_endpoints_match_abi(self, chain_outputs):
        """All 69 selected endpoints in payload_endpoints.json must be in framework_import.json."""
        if not chain_outputs["fw_json"].exists() or not chain_outputs["ep_json"].exists():
            pytest.skip("Required files not generated")
        fi = json.loads(chain_outputs["fw_json"].read_text())
        ep = json.loads(chain_outputs["ep_json"].read_text())
        # rx_endpoints and tx_endpoints are lists of endpoint_id strings
        all_ep_ids = set(fi.get("rx_endpoints", [])) | set(fi.get("tx_endpoints", []))
        selected = [e for e in ep.get("endpoints", []) if e.get("include_in_framework")]
        for e in selected:
            assert e.get("endpoint_id") in all_ep_ids, \
                f"Selected endpoint {e.get('endpoint_id')} not in framework import"

    def test_normalized_mapping_csc_count(self, chain_outputs):
        """OMTF DESIGN: exactly 52 CSC RX channels selected."""
        if not chain_outputs["mapping_json"].exists():
            pytest.skip()
        data = json.loads(chain_outputs["mapping_json"].read_text())
        csc_rx = [e for e in data.get("endpoints", [])
                  if e.get("include_in_framework") and e.get("direction") == "rx"
                  and e.get("link_function") in ("CSC",)]
        assert len(csc_rx) == 52, f"Expected 52 CSC RX, got {len(csc_rx)}"

    def test_normalized_mapping_bmt_l1_count(self, chain_outputs):
        """OMTF DESIGN: exactly 15 BMT-L1 RX channels selected."""
        if not chain_outputs["mapping_json"].exists():
            pytest.skip()
        data = json.loads(chain_outputs["mapping_json"].read_text())
        bmt = [e for e in data.get("endpoints", [])
               if e.get("include_in_framework") and e.get("direction") == "rx"
               and e.get("link_function") in ("BMT-L1",)]
        assert len(bmt) == 15, f"Expected 15 BMT-L1 RX, got {len(bmt)}"
