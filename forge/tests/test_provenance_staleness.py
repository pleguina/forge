"""
Tests for forge.core.provenance_staleness (release-plan Phase 5 slice
5.2) — the content-hash confirmation primitive shared by
forge/core/stale_detection.py and forge/verify/stale_artifact.py.
"""

from __future__ import annotations

from pathlib import Path

from forge.core.provenance_staleness import confirms_fresh
from forge.ir.provenance import ProvenanceManifest, write_provenance


def test_confirms_fresh_returns_none_when_no_manifest(tmp_path: Path):
    source = tmp_path / "design.yml"
    source.write_text("v1")

    assert confirms_fresh(source, tmp_path) is None


def test_confirms_fresh_returns_none_when_manifest_unreadable(tmp_path: Path):
    source = tmp_path / "design.yml"
    source.write_text("v1")
    (tmp_path / "provenance.json").write_text("not json{{{")

    assert confirms_fresh(source, tmp_path) is None


def test_confirms_fresh_returns_none_when_source_not_tracked(tmp_path: Path):
    source = tmp_path / "design.yml"
    source.write_text("v1")
    write_provenance(
        tmp_path / "provenance.json",
        ProvenanceManifest(source_hashes={"unrelated.yml": "deadbeef"}),
    )

    assert confirms_fresh(source, tmp_path) is None


def test_confirms_fresh_true_when_hash_matches_via_source_hashes(tmp_path: Path):
    from forge.core.utils.content_hash import hash_file

    source = tmp_path / "design.yml"
    source.write_text("unchanged content")
    write_provenance(
        tmp_path / "provenance.json",
        ProvenanceManifest(source_hashes={"design.yml": hash_file(source)}),
    )

    assert confirms_fresh(source, tmp_path) is True


def test_confirms_fresh_true_when_hash_matches_via_output_hashes(tmp_path: Path):
    """A source in one checker's terms can be an output in gen-top's own
    terms (e.g. checking a DUT RTL file gen-top itself wrote)."""
    from forge.core.utils.content_hash import hash_file

    source = tmp_path / "algo_top.v"
    source.write_text("module algo_top; endmodule")
    write_provenance(
        tmp_path / "provenance.json",
        ProvenanceManifest(output_hashes={"algo_top.v": hash_file(source)}),
    )

    assert confirms_fresh(source, tmp_path) is True


def test_confirms_fresh_false_when_content_genuinely_changed(tmp_path: Path):
    source = tmp_path / "design.yml"
    source.write_text("v1")
    write_provenance(
        tmp_path / "provenance.json",
        ProvenanceManifest(source_hashes={"design.yml": "hash-of-v1-not-what-is-on-disk"}),
    )

    assert confirms_fresh(source, tmp_path) is False


def test_confirms_fresh_falls_back_to_bare_filename_key(tmp_path: Path):
    """A manifest built with a different design directory than
    provenance_dir still resolves via a bare-filename fallback lookup."""
    from forge.core.utils.content_hash import hash_file

    nested = tmp_path / "nested"
    nested.mkdir()
    source = nested / "modules.yml"
    source.write_text("registry content")
    # Manifest lives one level up (provenance_dir == tmp_path), but its
    # key is a bare filename, not a path relative to provenance_dir/nested.
    write_provenance(
        tmp_path / "provenance.json",
        ProvenanceManifest(source_hashes={"modules.yml": hash_file(source)}),
    )

    assert confirms_fresh(source, tmp_path) is True


def test_confirms_fresh_returns_none_when_source_missing(tmp_path: Path):
    write_provenance(
        tmp_path / "provenance.json",
        ProvenanceManifest(source_hashes={"design.yml": "whatever"}),
    )
    assert confirms_fresh(tmp_path / "design.yml", tmp_path) is None
