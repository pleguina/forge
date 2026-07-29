"""Unit tests for forge.verify.dataset_adapter (Phase 7, slice 7.4b — layer B).

Covers the registry (explicit adapter_id selection, never suffix) and
CanonicalDataset/DatasetSource shapes. Real end-to-end coverage of
passthrough_demo's real `passthrough.identity-xml` adapter, driven through
an actual xsim run, lives in tests/verify/test_verify_run_xsim.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.verify.dataset_adapter import (
    CanonicalDataset,
    DatasetSource,
    get_dataset_adapter,
    list_registered_dataset_adapters,
    register_dataset_adapter,
)
from forge.verify.dataset_format import (
    DATASET_SCHEMA,
    DatasetMetadata,
    EnvironmentMetadata,
    SemanticMetadata,
    SerializedDataset,
)


def _dataset(events=None) -> SerializedDataset:
    events = events if events is not None else [{"x": 1}]
    return SerializedDataset(
        events=events,
        metadata=DatasetMetadata(
            schema=DATASET_SCHEMA,
            event_ids=[str(i) for i in range(len(events))],
            semantic=SemanticMetadata(source_content_hash="h"),
            environment=EnvironmentMetadata(source_path="d.xml", generated_at="now"),
        ),
    )


class _IdentityAdapter:
    adapter_id = "test.identity"
    adapter_version = "1.0"

    def materialize(self, source: DatasetSource, config) -> CanonicalDataset:
        assert source.serialized is not None
        return CanonicalDataset(events=source.serialized.events, metadata=source.serialized.metadata)


class _RequiresRawAdapter:
    adapter_id = "test.raw-only"
    adapter_version = "1.0"

    def materialize(self, source: DatasetSource, config) -> CanonicalDataset:
        if source.raw_path is None:
            raise ValueError("requires raw_path")
        return CanonicalDataset(events=[{"path": str(source.raw_path)}], metadata=_dataset().metadata)


# ── Registry ─────────────────────────────────────────────────────────────

def test_register_and_get_dataset_adapter_by_explicit_id():
    register_dataset_adapter(_IdentityAdapter.adapter_id, _IdentityAdapter())
    resolved = get_dataset_adapter("test.identity")
    assert resolved.adapter_id == "test.identity"


def test_get_dataset_adapter_unknown_id_raises():
    with pytest.raises(ValueError, match="No dataset adapter registered"):
        get_dataset_adapter("nonexistent.adapter.id")


def test_list_registered_dataset_adapters_includes_registered_ids():
    register_dataset_adapter(_IdentityAdapter.adapter_id, _IdentityAdapter())
    assert "test.identity" in list_registered_dataset_adapters()


def test_adapter_selection_is_explicit_never_inferred_from_suffix():
    """Two adapters, both potentially applicable to a `.xml` source, must
    resolve to genuinely different objects purely by declared id — suffix
    is never consulted at this layer."""
    identity = _IdentityAdapter()
    raw_only = _RequiresRawAdapter()
    register_dataset_adapter("test.suffix-parity.a", identity)
    register_dataset_adapter("test.suffix-parity.b", raw_only)

    resolved_a = get_dataset_adapter("test.suffix-parity.a")
    resolved_b = get_dataset_adapter("test.suffix-parity.b")
    assert resolved_a is identity
    assert resolved_b is raw_only
    assert resolved_a is not resolved_b


# ── materialize() behavior ───────────────────────────────────────────────

def test_identity_adapter_materialize_is_a_genuine_no_op():
    dataset = _dataset(events=[{"a": 1}, {"a": 2}])
    source = DatasetSource(serialized=dataset)
    result = _IdentityAdapter().materialize(source, {})
    assert isinstance(result, CanonicalDataset)
    assert result.events == dataset.events
    assert result.metadata == dataset.metadata


def test_raw_path_only_adapter_rejects_serialized_source():
    source = DatasetSource(serialized=_dataset())
    with pytest.raises(ValueError, match="requires raw_path"):
        _RequiresRawAdapter().materialize(source, {})


def test_raw_path_source_bypasses_layer_a_entirely():
    source = DatasetSource(raw_path=Path("/data/some_capture.pcap"))
    result = _RequiresRawAdapter().materialize(source, {})
    assert result.events == [{"path": "/data/some_capture.pcap"}]


def test_dataset_source_defaults_to_neither_set():
    source = DatasetSource()
    assert source.serialized is None
    assert source.raw_path is None


# ── design.verification.yml `dataset.adapter` field ─────────────────────

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_real_passthrough_demo_design_declares_its_adapter():
    """The real, committed passthrough_demo design.verification.yml
    declares `dataset.adapter: passthrough.identity-xml` for real — not a
    synthetic fixture."""
    from forge.verify.design_contract import load_verify_design

    design_yml = REPO_ROOT / "plugins/passthrough_demo/forge/verify/design.verification.yml"
    contract = load_verify_design(design_yml)
    dataset = contract.get_dataset("passthrough_demo_golden")
    assert dataset is not None
    assert dataset.adapter == "passthrough.identity-xml"


def test_dataset_adapter_field_defaults_to_none_when_absent(tmp_path: Path):
    import textwrap

    from forge.verify.design_contract import load_verify_design

    design_yml = tmp_path / "design.verification.yml"
    design_yml.write_text(textwrap.dedent("""
        plugin: fake_plugin
        datasets:
          fake_golden:
            xml: schemas/data/fake_golden.xml
        flows:
          - name: fake_flow
            kind: full_chip_rtl
            backend: xsim
            top_module: fake_top
            tb_module: tb_fake_top
            dut_rtl_source: gen-top/fake
            dataset: fake_golden
    """))
    contract = load_verify_design(design_yml)
    assert contract.get_dataset("fake_golden").adapter is None
