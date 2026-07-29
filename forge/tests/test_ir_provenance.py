"""
Tests for forge.ir.provenance — content-hash provenance for the canonical
IR (release-plan Phase 5, first slice). See the module docstring for what
this does and does not replace (the existing mtime-based
forge/core/stale_detection.py and forge/verify/stale_artifact.py are
untouched this session).
"""

from __future__ import annotations

from pathlib import Path

from forge.ir.build import build_project_ir
from forge.ir.provenance import (
    ProvenanceManifest,
    build_provenance,
    explain_staleness,
    read_provenance,
    write_provenance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_YML = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
MODULES_YML = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"


def test_build_provenance_hashes_real_source_files():
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    manifest = build_provenance(project, command_options={"contracts_from": str(MODULES_YML)})

    assert manifest.ir_content_hash
    assert manifest.project_name == "design"
    assert str(DESIGN_YML) in manifest.source_hashes
    # The passthrough interface contract file should also be hashed.
    assert any("passthrough.interface.yaml" in k for k in manifest.source_hashes)


def test_provenance_write_read_round_trip(tmp_path):
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    manifest = build_provenance(project)

    out = tmp_path / "provenance.json"
    write_provenance(out, manifest)
    loaded = read_provenance(out)

    assert loaded.ir_content_hash == manifest.ir_content_hash
    assert loaded.source_hashes == manifest.source_hashes
    assert loaded.generated_at is not None  # written, but...


def test_generated_at_is_never_compared_for_staleness():
    a = ProvenanceManifest(ir_content_hash="x", generated_at="2020-01-01T00:00:00")
    b = ProvenanceManifest(ir_content_hash="x", generated_at="2099-01-01T00:00:00")

    result = explain_staleness(a, b)
    assert result.stale is False
    assert result.reasons == []


def test_explain_staleness_identical_manifests_are_fresh():
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    a = build_provenance(project)
    b = build_provenance(project)

    result = explain_staleness(a, b)
    assert result.stale is False
    assert result.reasons == []


def test_explain_staleness_detects_changed_source_file(tmp_path):
    src = tmp_path / "input.txt"
    src.write_text("v1")

    a = ProvenanceManifest(ir_content_hash="h", source_hashes={str(src): "hash-v1"})
    b = ProvenanceManifest(ir_content_hash="h", source_hashes={str(src): "hash-v2"})

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("changed input" in r and str(src) in r for r in result.reasons)


def test_explain_staleness_detects_ir_content_change():
    a = ProvenanceManifest(ir_content_hash="hash-a")
    b = ProvenanceManifest(ir_content_hash="hash-b")

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("canonical IR content changed" in r for r in result.reasons)


def test_explain_staleness_detects_added_and_removed_inputs():
    a = ProvenanceManifest(ir_content_hash="h", source_hashes={"old.yml": "h1"})
    b = ProvenanceManifest(ir_content_hash="h", source_hashes={"new.yml": "h2"})

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("new input file: new.yml" in r for r in result.reasons)
    assert any("input file no longer present: old.yml" in r for r in result.reasons)


def test_explain_staleness_detects_forge_version_change():
    a = ProvenanceManifest(ir_content_hash="h", forge_version="1.0.0")
    b = ProvenanceManifest(ir_content_hash="h", forge_version="2.0.0")

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("FORGE version changed" in r for r in result.reasons)


def test_explain_staleness_detects_schema_version_change():
    a = ProvenanceManifest(ir_content_hash="h", schema_version="0.1.0")
    b = ProvenanceManifest(ir_content_hash="h", schema_version="0.2.0")

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("provenance schema version changed" in r for r in result.reasons)


def test_explain_staleness_detects_command_option_change():
    a = ProvenanceManifest(ir_content_hash="h", command_options={"strict": False})
    b = ProvenanceManifest(ir_content_hash="h", command_options={"strict": True})

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("command options changed" in r for r in result.reasons)
