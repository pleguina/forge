"""Integration tests for trigger_demo verify plugin.

Validates that:
  * trigger_demo plugin bootstraps correctly
  * csim backend is registered and accessible
  * xsim backend is registered and accessible
  * verify.flow.yml loads for both csim and xsim flows
  * design.verification.yml loads correctly (8 flows: 4 csim + 4 xsim, 1 dataset)
  * Backend adapter interfaces are fully implemented
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# conftest.py already bootstrapped trigger_demo and set up sys.path.
from fw_verify.backend_base import BackendAdapter, BackendCapabilities
from fw_verify.backend_registry import get_adapter, list_supported_backends
from fw_verify.design_contract import load_verify_design
from fw_verify.flow_loader import FlowConfig, load_generic_flow
from fw_verify.plugin_registry import is_plugin_bootstrapped

# ── Paths ─────────────────────────────────────────────────────────────────

_TOOLS = Path(__file__).parent.parent.resolve()
_PLUGIN_VERIFY = _TOOLS.parent
_REPO_ROOT = _TOOLS.parents[4]


# ═══════════════════════════════════════════════════════════════════════════
# Bootstrap tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBootstrap:

    def test_plugin_is_bootstrapped(self) -> None:
        assert is_plugin_bootstrapped("trigger_demo")

    def test_csim_backend_registered(self) -> None:
        assert "csim" in list_supported_backends()

    def test_xsim_backend_registered(self) -> None:
        assert "xsim" in list_supported_backends()

    def test_csim_adapter_is_backend_adapter(self) -> None:
        adapter = get_adapter("csim")
        assert isinstance(adapter, BackendAdapter)

    def test_xsim_adapter_is_backend_adapter(self) -> None:
        adapter = get_adapter("xsim")
        assert isinstance(adapter, BackendAdapter)

    def test_csim_backend_id(self) -> None:
        adapter = get_adapter("csim")
        assert adapter.backend_id == "csim"

    def test_xsim_backend_id(self) -> None:
        adapter = get_adapter("xsim")
        assert adapter.backend_id == "xsim"


# ═══════════════════════════════════════════════════════════════════════════
# Backend adapter interface tests
# ═══════════════════════════════════════════════════════════════════════════

class TestXsimAdapter:

    @pytest.fixture()
    def adapter(self) -> BackendAdapter:
        return get_adapter("xsim")

    def test_compatible_flow_kinds(self, adapter: BackendAdapter) -> None:
        assert "single_module_rtl" in adapter.compatible_flow_kinds
        assert "full_chip_rtl" in adapter.compatible_flow_kinds

    def test_capabilities_type(self, adapter: BackendAdapter) -> None:
        caps = adapter.capabilities
        assert isinstance(caps, BackendCapabilities)

    def test_capabilities_waveform(self, adapter: BackendAdapter) -> None:
        assert adapter.capabilities.supports_waveform is True

    def test_capabilities_requires_vendor_env(self, adapter: BackendAdapter) -> None:
        assert adapter.capabilities.requires_vendor_env is True

    def test_required_tools(self, adapter: BackendAdapter) -> None:
        assert "xvlog" in adapter.required_tools
        assert "xelab" in adapter.required_tools
        assert "xsim" in adapter.required_tools

    def test_required_artifacts_rtl_top(self, adapter: BackendAdapter) -> None:
        art_names = {a.name for a in adapter.required_artifacts}
        assert "rtl_top" in art_names


class TestCsimAdapter:

    @pytest.fixture()
    def adapter(self) -> BackendAdapter:
        return get_adapter("csim")

    def test_compatible_flow_kinds(self, adapter: BackendAdapter) -> None:
        assert "hls_csim" in adapter.compatible_flow_kinds

    def test_capabilities_type(self, adapter: BackendAdapter) -> None:
        caps = adapter.capabilities
        assert isinstance(caps, BackendCapabilities)

    def test_capabilities_no_waveform(self, adapter: BackendAdapter) -> None:
        assert adapter.capabilities.supports_waveform is False

    def test_capabilities_no_vendor_env(self, adapter: BackendAdapter) -> None:
        assert adapter.capabilities.requires_vendor_env is False

    def test_required_artifacts_declared(self, adapter: BackendAdapter) -> None:
        arts = adapter.required_artifacts
        assert isinstance(arts, tuple)
        assert len(arts) >= 1

    def test_required_tools_empty(self, adapter: BackendAdapter) -> None:
        assert adapter.required_tools == ()


# ═══════════════════════════════════════════════════════════════════════════
# Flow file loading tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFlowLoading:

    @pytest.fixture()
    def flow_yml(self) -> Path:
        return _PLUGIN_VERIFY / "hit_decoder_csim" / "verify.flow.yml"

    def test_flow_file_exists(self, flow_yml: Path) -> None:
        assert flow_yml.exists()

    def test_load_generic_flow(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert isinstance(cfg, FlowConfig)

    def test_flow_plugin_id(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.plugin_id == "trigger_demo"

    def test_flow_name(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.flow_name == "hit_decoder_csim"

    def test_flow_kind(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.flow_kind == "hls_csim"

    def test_flow_backend(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.backend == "csim"

    def test_flow_top_module(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.top_module == "hit_decoder"

    def test_flow_tb_module(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.tb_module == "tb_hit_decoder"

    def test_flow_dut_manifest_is_none(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.dut_manifest is None

    def test_flow_dut_port_map_is_none(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.dut_port_map is None


# ═══════════════════════════════════════════════════════════════════════════
# XSIM flow file loading tests
# ═══════════════════════════════════════════════════════════════════════════

class TestXsimFlowLoading:

    @pytest.fixture()
    def flow_yml(self) -> Path:
        return _PLUGIN_VERIFY / "hit_decoder_xsim" / "verify.flow.yml"

    def test_flow_file_exists(self, flow_yml: Path) -> None:
        assert flow_yml.exists()

    def test_load_generic_xsim_flow(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert isinstance(cfg, FlowConfig)

    def test_xsim_flow_plugin(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.plugin_id == "trigger_demo"

    def test_xsim_flow_kind(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.flow_kind == "single_module_rtl"

    def test_xsim_flow_backend(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.backend == "xsim"

    def test_xsim_flow_tb_module(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.tb_module == "tb_hit_decoder_xsim"

    def test_xsim_flow_dut_rtl_set(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.dut_rtl is not None
        assert "hit_decoder.v" in str(cfg.dut_rtl)

    def test_xsim_flow_null_manifest(self, flow_yml: Path) -> None:
        cfg = load_generic_flow(flow_yml, consumer_root=_REPO_ROOT)
        assert cfg.dut_manifest is None

    @pytest.mark.parametrize("module", [
        "hit_decoder_xsim",
        "hit_collector_xsim",
        "trigger_logic_xsim",
        "trigger_output_xsim",
    ])
    def test_all_xsim_flows_load(self, module: str) -> None:
        yml = _PLUGIN_VERIFY / module / "verify.flow.yml"
        assert yml.exists(), f"verify.flow.yml missing: {yml}"
        cfg = load_generic_flow(yml, consumer_root=_REPO_ROOT)
        assert cfg.flow_kind == "single_module_rtl"
        assert cfg.backend == "xsim"

    @pytest.mark.parametrize("module", [
        "hit_decoder_xsim",
        "hit_collector_xsim",
        "trigger_logic_xsim",
        "trigger_output_xsim",
    ])
    def test_all_xsim_svs_exist(self, module: str) -> None:
        """Each xsim flow directory must contain an SV testbench."""
        flow_dir = _PLUGIN_VERIFY / module
        tb_name = f"tb_{module}.sv"
        assert (flow_dir / tb_name).exists(), f"SV testbench missing: {tb_name}"


# ═══════════════════════════════════════════════════════════════════════════
# Design contract tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDesignContract:

    @pytest.fixture()
    def contract(self):
        return load_verify_design(_PLUGIN_VERIFY / "design.verification.yml")

    def test_contract_plugin(self, contract) -> None:
        assert contract.plugin == "trigger_demo"

    def test_contract_has_dataset(self, contract) -> None:
        ds = contract.get_dataset("trigger_demo_golden")
        assert ds is not None

    def test_dataset_xml_path(self, contract) -> None:
        ds = contract.get_dataset("trigger_demo_golden")
        assert "trigger_demo_golden.xml" in ds.xml

    def test_nine_flows(self, contract) -> None:
        assert len(contract.flows) == 9

    def test_csim_flow_names(self, contract) -> None:
        names = {f.name for f in contract.flows if f.backend == "csim"}
        expected = {
            "hit_decoder_csim",
            "hit_collector_csim",
            "trigger_logic_csim",
            "trigger_output_csim",
        }
        assert names == expected

    def test_xsim_flow_names(self, contract) -> None:
        names = {f.name for f in contract.flows if f.backend == "xsim"}
        expected = {
            "hit_decoder_xsim",
            "hit_collector_xsim",
            "trigger_logic_xsim",
            "trigger_output_xsim",
            "trigger_pipeline_xsim",
        }
        assert names == expected

    def test_csim_flows_are_hls_csim(self, contract) -> None:
        for f in contract.flows:
            if f.backend == "csim":
                assert f.kind == "hls_csim", f"{f.name}: kind={f.kind}"

    def test_xsim_flows_are_single_module_rtl(self, contract) -> None:
        for f in contract.flows:
            if f.backend == "xsim" and f.name != "trigger_pipeline_xsim":
                assert f.kind == "single_module_rtl", f"{f.name}: kind={f.kind}"

    def test_pipeline_flow_is_full_chip_rtl(self, contract) -> None:
        flow = contract.get_flow("trigger_pipeline_xsim")
        assert flow is not None
        assert flow.kind == "full_chip_rtl"
        assert flow.top_module == "algo_top"

    def test_pipeline_flow_uses_generated_topology(self, contract) -> None:
        flow = contract.get_flow("trigger_pipeline_xsim")
        assert flow is not None
        assert flow.dut_rtl_source == "gen-top/design_trigger_demo_pipeline"

    def test_all_flows_reference_dataset(self, contract) -> None:
        for f in contract.flows:
            assert f.dataset == "trigger_demo_golden", f"{f.name}: {f.dataset}"

    def test_xsim_flow_lookup(self, contract) -> None:
        flow = contract.get_flow("hit_decoder_xsim")
        assert flow is not None
        assert flow.backend == "xsim"

    def test_defaults_clk_period(self, contract) -> None:
        assert contract.defaults.clk_period_ns == 4.0

    def test_defaults_reset_cycles(self, contract) -> None:
        assert contract.defaults.reset_cycles == 4


# ═══════════════════════════════════════════════════════════════════════════
# Synthetic flow tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSyntheticFlow:
    """Tests with a temporary verify.flow.yml — no dependency on real paths."""

    def _write_flow(self, tmp_path: Path) -> Path:
        p = tmp_path / "verify.flow.yml"
        p.write_text(textwrap.dedent("""\
            flow:
              plugin:     trigger_demo
              name:       synthetic_csim
              kind:       hls_csim
              backend:    csim
              top_module: my_ip
              tb_module:  tb_my_ip

            dut:
              rtl:            out/my_ip.v
              manifest:       ~
              port_map:       ~
              tb_bindings:    ~
              port_signature: ~

            dataset:
              xml:              data/test.xml
              default_event_id: 1

            simulation:
              clk_period_ns:              5.0
              reset_cycles:               2
              idle_cycles_after_reset:    1
              post_stimulus_drain_cycles: 3
        """))
        # create minimal dut.rtl so path resolution doesn't fail
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "my_ip.v").write_text("// stub")
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "test.xml").write_text("<events/>")
        return p

    def test_synthetic_flow_loads(self, tmp_path: Path) -> None:
        p = self._write_flow(tmp_path)
        cfg = load_generic_flow(p, consumer_root=tmp_path)
        assert cfg.flow_name == "synthetic_csim"

    def test_synthetic_flow_kind(self, tmp_path: Path) -> None:
        p = self._write_flow(tmp_path)
        cfg = load_generic_flow(p, consumer_root=tmp_path)
        assert cfg.flow_kind == "hls_csim"

    def test_synthetic_flow_timing(self, tmp_path: Path) -> None:
        p = self._write_flow(tmp_path)
        cfg = load_generic_flow(p, consumer_root=tmp_path)
        assert cfg.sim_clk_period_ns == 5.0
        assert cfg.sim_reset_cycles == 2
