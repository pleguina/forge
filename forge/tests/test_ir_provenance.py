"""
Tests for forge.ir.provenance — content-hash provenance for the canonical
IR. See the module docstring for what
this does and does not replace (the existing mtime-based
forge/core/stale_detection.py and forge/verification/stale_artifact.py are
untouched this session).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from forge.ir.build import build_project_ir
from forge.ir.provenance import (
    ProvenanceManifest,
    build_provenance,
    explain_staleness,
    read_provenance,
    write_provenance,
)
from forge.ir.serialize import content_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_YML = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
MODULES_YML = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"


def test_build_provenance_hashes_real_source_files():
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    manifest = build_provenance(project, command_options={"contracts_from": str(MODULES_YML)})

    assert manifest.ir_content_hash
    assert manifest.project_name == "design"
    # Keys are relative to design.yml's own directory, not
    # absolute paths — the design file itself keys as its own bare name.
    assert "design.yml" in manifest.source_hashes
    # The passthrough interface contract file should also be hashed.
    assert any("passthrough.interface.yaml" in k for k in manifest.source_hashes)
    # ...and none of the keys leak an absolute filesystem path.
    for key in manifest.source_hashes:
        assert not Path(key).is_absolute(), key


def _copy_passthrough_demo(dest_root: Path) -> "tuple[Path, Path]":
    """Copy the real passthrough_demo plugin to *dest_root*, simulating a
    checkout at a different absolute location. Returns (design_yml, modules_yml)."""
    plugin_dest = dest_root / "plugins" / "passthrough_demo"
    shutil.copytree(REPO_ROOT / "plugins" / "passthrough_demo", plugin_dest)
    return (
        plugin_dest / "forge/designs/design.yml",
        plugin_dest / "forge/modules.yml",
    )


def test_content_hash_is_portable_across_absolute_checkout_paths(tmp_path):
    """Regression: the real bug fixed here was
    ``content_hash()`` changing when only the caller's absolute filesystem
    layout changed (``design.source.file``), not the design's actual
    content. Two independent checkouts of the same real design at
    different absolute paths must hash identically."""
    design_a, modules_a = _copy_passthrough_demo(tmp_path / "checkout_a")
    design_b, modules_b = _copy_passthrough_demo(tmp_path / "checkout_b" / "nested")

    project_a = build_project_ir(design_a, contracts_from=modules_a)
    project_b = build_project_ir(design_b, contracts_from=modules_b)

    # Sanity: the two checkouts really do live at different absolute paths.
    assert project_a.design.source.file != project_b.design.source.file
    assert content_hash(project_a) == content_hash(project_b)


def test_source_hashes_keys_are_portable_across_absolute_checkout_paths(tmp_path):
    """Same regression as above, for ``build_provenance``'s
    ``source_hashes`` keys (previously ``str(absolute_path)``)."""
    design_a, modules_a = _copy_passthrough_demo(tmp_path / "checkout_a")
    design_b, modules_b = _copy_passthrough_demo(tmp_path / "checkout_b" / "nested")

    project_a = build_project_ir(design_a, contracts_from=modules_a)
    project_b = build_project_ir(design_b, contracts_from=modules_b)

    manifest_a = build_provenance(project_a)
    manifest_b = build_provenance(project_b)

    assert manifest_a.source_hashes == manifest_b.source_hashes


def test_content_hash_is_portable_when_a_diagnostic_carries_a_location(tmp_path):
    """A second, independent instance of the same portable-hash bug class,
    found by the YAML-key-ordering determinism test
    (test_ir_build.py::test_content_hash_is_independent_of_yaml_top_level_key_order),
    not by the original investigation: every validation
    diagnostic attached to the design carries a `location.file` stamped
    with the resolved absolute design.yml path
    (`forge/ir/build.py`). A module declaring an HLS module with a
    non-C++ source file (`.v` instead of `.cpp`) reliably produces one —
    real content, two different absolute tmp directories, must still hash
    identically."""
    module_block = "  - name: pt\n    kind: hls\n    top: passthrough\n    src: [passthrough.v]\n"
    design_yml = f"part: xcvu13p\nclock_period: 4.0\nmodules:\n{module_block}"

    def _write(root: Path) -> Path:
        root.mkdir(parents=True)
        (root / "design.yml").write_text(design_yml)
        (root / "passthrough.v").write_text("module passthrough; endmodule")
        return root / "design.yml"

    design_a = _write(tmp_path / "checkout_a")
    design_b = _write(tmp_path / "checkout_b" / "nested")

    project_a = build_project_ir(design_a)
    project_b = build_project_ir(design_b)

    # Sanity: a diagnostic with a real, differing absolute location was
    # actually produced by both builds — otherwise this test would prove
    # nothing.
    located = [d for d in project_a.design.diagnostics if d.location is not None]
    assert located
    assert located[0].location.file != next(
        d.location.file for d in project_b.design.diagnostics if d.location is not None
    )
    assert content_hash(project_a) == content_hash(project_b)


def test_provenance_write_read_round_trip(tmp_path):
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    manifest = build_provenance(project)

    out = tmp_path / "provenance.json"
    write_provenance(out, manifest)
    loaded = read_provenance(out)

    assert loaded.ir_content_hash == manifest.ir_content_hash
    assert loaded.source_hashes == manifest.source_hashes
    assert loaded.generated_at is not None  # written, but...


# ---------------------------------------------------------------------------
# plan_hash / toolchain_versions / output_hashes / project_identity
# ---------------------------------------------------------------------------

def test_build_provenance_defaults_new_fields_to_honest_absence():
    """forge inspect's read-only IR-only path: no plan exists (a plan
    requires generation intent) and nothing was generated to hash — these
    must stay honestly empty/None, not fabricated."""
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    manifest = build_provenance(project)

    assert manifest.plan_hash is None
    assert manifest.output_hashes == {}
    assert manifest.project_identity is None
    # toolchain_versions IS auto-populated (an environment fact, not
    # generation-specific) — see the dedicated test below for its shape.


def test_build_provenance_toolchain_versions_auto_populated_by_default():
    """Unlike plan_hash/output_hashes/project_identity, toolchain_versions
    doesn't require generation-specific context, so build_provenance
    populates it itself via forge.core.toolchain_versions — never raising
    even when no toolchains are installed (this dev environment has
    none)."""
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    manifest = build_provenance(project)

    assert isinstance(manifest.toolchain_versions, dict)
    for tool, version in manifest.toolchain_versions.items():
        assert isinstance(tool, str) and isinstance(version, str)


def test_build_provenance_accepts_caller_supplied_generation_fields(tmp_path):
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)

    out1 = tmp_path / "artifact_one.txt"
    out1.write_text("hello")
    out2 = tmp_path / "nested" / "artifact_two.txt"
    out2.parent.mkdir()
    out2.write_text("world")

    manifest = build_provenance(
        project,
        plan_hash="deadbeef" * 8,
        output_paths=[out1, out2, tmp_path / "does_not_exist.txt"],
        project_identity="trigger_demo",
        toolchain_versions={"ghdl": "1.2.3"},
    )

    assert manifest.plan_hash == "deadbeef" * 8
    assert manifest.project_identity == "trigger_demo"
    assert manifest.toolchain_versions == {"ghdl": "1.2.3"}
    # Missing output paths are silently skipped (build_provenance only
    # hashes files that exist), same convention as source_hashes.
    assert len(manifest.output_hashes) == 2
    assert all(not Path(k).is_absolute() for k in manifest.output_hashes)


def test_provenance_round_trip_preserves_slice_5_1_fields(tmp_path):
    project = build_project_ir(DESIGN_YML, contracts_from=MODULES_YML)
    manifest = build_provenance(
        project,
        plan_hash="cafebabe" * 8,
        project_identity="trigger_demo",
        toolchain_versions={"verilator": "5.0.0"},
    )

    out = tmp_path / "provenance.json"
    write_provenance(out, manifest)
    loaded = read_provenance(out)

    assert loaded.plan_hash == manifest.plan_hash
    assert loaded.project_identity == manifest.project_identity
    assert loaded.toolchain_versions == manifest.toolchain_versions
    assert loaded.output_hashes == manifest.output_hashes


def test_provenance_schema_version_is_0_2_0():
    """PROVENANCE_SCHEMA_VERSION was bumped for the source_hashes
    key-format change; the newer fields are purely additive and
    don't need a further bump."""
    from forge.ir.provenance import PROVENANCE_SCHEMA_VERSION
    assert PROVENANCE_SCHEMA_VERSION == "0.2.0"
    assert ProvenanceManifest().schema_version == "0.2.0"


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
    """A previous->current schema bump that's still >= the current known
    minimum (i.e. not the slice-5.0-era 0.1.0->0.2.0 boundary — see
    test_explain_staleness_flags_unsupported_old_manifest below) is
    reported as an ordinary field diff, same as any other field."""
    a = ProvenanceManifest(ir_content_hash="h", schema_version="0.2.0")
    b = ProvenanceManifest(ir_content_hash="h", schema_version="0.3.0")

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("provenance schema version changed" in r for r in result.reasons)


def test_explain_staleness_flags_unsupported_old_manifest():
    """A previous manifest older than PROVENANCE_SCHEMA_VERSION
    (e.g. 0.1.0, predating the source_hashes key-format change)
    must not be diffed field-by-field — its absolute-path keys would
    silently misreport as both "removed" and "added" against a current
    manifest's relative-path keys, a false diff rather than a true
    staleness signal. It's flagged as unsupported instead, and nothing
    else is compared."""
    a = ProvenanceManifest(ir_content_hash="h", schema_version="0.1.0", source_hashes={"/abs/design.yml": "x"})
    b = ProvenanceManifest(ir_content_hash="h", schema_version="0.2.0", source_hashes={"design.yml": "x"})

    result = explain_staleness(a, b)
    assert result.stale is True
    assert len(result.reasons) == 1
    assert "unsupported old manifest" in result.reasons[0]
    assert "0.1.0" in result.reasons[0]


def test_explain_staleness_detects_command_option_change():
    a = ProvenanceManifest(ir_content_hash="h", command_options={"strict": False})
    b = ProvenanceManifest(ir_content_hash="h", command_options={"strict": True})

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("command options changed" in r for r in result.reasons)


def test_explain_staleness_detects_changed_tool_version():
    """A changed tool version is a staleness reason, now meaningful since
    toolchain_versions is actually populated for real."""
    a = ProvenanceManifest(ir_content_hash="h", toolchain_versions={"ghdl": "GHDL 3.0.0"})
    b = ProvenanceManifest(ir_content_hash="h", toolchain_versions={"ghdl": "GHDL 4.1.0"})

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any(
        "changed tool version: ghdl" in r and "GHDL 3.0.0" in r and "GHDL 4.1.0" in r
        for r in result.reasons
    )


def test_explain_staleness_detects_new_and_removed_toolchains():
    a = ProvenanceManifest(ir_content_hash="h", toolchain_versions={"ghdl": "GHDL 4.1.0"})
    b = ProvenanceManifest(ir_content_hash="h", toolchain_versions={"verilator": "5.022"})

    result = explain_staleness(a, b)
    assert result.stale is True
    assert any("new toolchain detected: verilator" in r for r in result.reasons)
    assert any("toolchain no longer detected: ghdl" in r for r in result.reasons)


def test_explain_staleness_identical_toolchain_versions_no_reason():
    a = ProvenanceManifest(ir_content_hash="h", toolchain_versions={"ghdl": "GHDL 4.1.0"})
    b = ProvenanceManifest(ir_content_hash="h", toolchain_versions={"ghdl": "GHDL 4.1.0"})

    result = explain_staleness(a, b)
    assert result.stale is False
    assert result.reasons == []
