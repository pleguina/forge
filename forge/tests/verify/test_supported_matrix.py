"""Unit tests for forge.verify.supported_matrix (Phase 7, slice 7.1).

MatrixEntry was reshaped from a single ``canonical_backend`` string to
``default_backend`` + always-populated ``allowed_backends`` — these tests
cover the membership-based validation this enables (multiple real backends
per flow kind) alongside the pre-existing classification behavior.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from forge.verify.supported_matrix import (
    EXPERIMENTAL_KINDS,
    MATRIX,
    SUPPORTED_MATRIX,
    FlowClassification,
    MatrixEntry,
    allowed_backends_for,
    classify_flow,
    default_backend_for,
    validate_flow_matrix,
)


def test_supported_matrix_lists_default_backends():
    assert SUPPORTED_MATRIX["hls_csim"] == "csim"
    assert SUPPORTED_MATRIX["single_module_rtl"] == "xsim"
    assert SUPPORTED_MATRIX["full_chip_rtl"] == "xsim"


def test_full_chip_rtl_allows_both_xsim_and_verilator():
    assert allowed_backends_for("full_chip_rtl") == frozenset({"xsim", "verilator"})
    assert allowed_backends_for("single_module_rtl") == frozenset({"xsim", "verilator"})


def test_hls_csim_allows_only_csim():
    assert allowed_backends_for("hls_csim") == frozenset({"csim"})


def test_default_backend_for_known_and_unknown_kinds():
    assert default_backend_for("full_chip_rtl") == "xsim"
    assert default_backend_for("nonexistent_kind") is None


def test_allowed_backends_for_unknown_kind_is_empty():
    assert allowed_backends_for("nonexistent_kind") == frozenset()


def test_classify_flow_unknown_kind_is_unsupported():
    assert classify_flow("nonexistent_kind", "xsim") == FlowClassification.UNSUPPORTED


def test_classify_flow_reduced_chain_rtl_is_experimental():
    assert classify_flow("reduced_chain_rtl", "xsim") == FlowClassification.EXPERIMENTAL
    assert "reduced_chain_rtl" in EXPERIMENTAL_KINDS


def test_validate_flow_matrix_accepts_default_backend():
    assert validate_flow_matrix("full_chip_rtl", "xsim") == []


def test_validate_flow_matrix_accepts_second_allowed_backend():
    assert validate_flow_matrix("full_chip_rtl", "verilator") == []


def test_validate_flow_matrix_rejects_backend_outside_allowed_set():
    errors = validate_flow_matrix("full_chip_rtl", "ghdl")
    assert errors
    assert "ghdl" in errors[0]
    assert "xsim" in errors[0] and "verilator" in errors[0]


def test_validate_flow_matrix_rejects_backend_for_single_backend_kind():
    errors = validate_flow_matrix("hls_csim", "xsim")
    assert errors
    assert "csim" in errors[0]


def test_validate_flow_matrix_unknown_kind_lists_recognised_kinds():
    errors = validate_flow_matrix("nonexistent_kind", "xsim")
    assert errors
    assert "full_chip_rtl" in errors[0]


def test_validate_flow_matrix_experimental_kind_without_opt_in_rejected():
    errors = validate_flow_matrix("reduced_chain_rtl", "xsim")
    assert errors
    assert "EXPERIMENTAL" in errors[0]


# ── MATRIX — the complete, real, runtime-immutable view (release-plan
# Phase 9, Defect 3) ─────────────────────────────────────────────────────

def test_matrix_is_a_mapping_proxy():
    assert isinstance(MATRIX, MappingProxyType)


def test_matrix_is_runtime_immutable():
    with pytest.raises(TypeError):
        MATRIX["new_kind"] = None  # type: ignore[index]


def test_matrix_entries_are_full_matrix_entry_objects():
    assert len(MATRIX) > 0
    for entry in MATRIX.values():
        assert isinstance(entry, MatrixEntry)
        assert isinstance(entry.allowed_backends, frozenset)
        assert entry.default_backend in entry.allowed_backends


def test_matrix_agrees_with_supported_and_experimental_views():
    for kind, backend in SUPPORTED_MATRIX.items():
        assert kind in MATRIX
        assert MATRIX[kind].classification == FlowClassification.SUPPORTED
        assert MATRIX[kind].default_backend == backend

    for kind, reason in EXPERIMENTAL_KINDS.items():
        assert kind in MATRIX
        assert MATRIX[kind].classification == FlowClassification.EXPERIMENTAL
        assert MATRIX[kind].reason == reason

    # MATRIX must not drop any classification SUPPORTED_MATRIX/EXPERIMENTAL_KINDS
    # can't represent (there is none today, but this guards against a future
    # kind being added to _MATRIX and silently missing from MATRIX).
    supported_and_experimental = set(SUPPORTED_MATRIX) | set(EXPERIMENTAL_KINDS)
    assert supported_and_experimental <= set(MATRIX)
