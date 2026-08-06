"""Tests for forge.core.stale_detection — staleness checks for top-generation
and IP-summary outputs relative to their source contracts.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from forge.core.stale_detection import (
    ArtifactStaleness,
    check_ip_info_staleness,
    check_top_gen_staleness,
    format_stale_report,
)


def _touch(path: Path, *, mtime: float | None = None, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestArtifactStaleness:
    def test_stale_when_source_newer(self):
        finding = ArtifactStaleness(
            artifact=Path("algo_top.v"), source=Path("design.yml"),
            artifact_mtime=100.0, source_mtime=200.0,
        )
        assert finding.stale is True

    def test_not_stale_when_artifact_newer(self):
        finding = ArtifactStaleness(
            artifact=Path("algo_top.v"), source=Path("design.yml"),
            artifact_mtime=200.0, source_mtime=100.0,
        )
        assert finding.stale is False

    def test_message_includes_both_paths(self):
        finding = ArtifactStaleness(
            artifact=Path("build_manifest.json"), source=Path("design.yml"),
            artifact_mtime=100.0, source_mtime=200.0,
        )
        msg = finding.message()
        assert "build_manifest.json" in msg
        assert "design.yml" in msg
        assert "is stale" in msg


class TestCheckTopGenStaleness:
    def test_no_sources_found_reports_missing(self, tmp_path: Path):
        report = check_top_gen_staleness(tmp_path)
        assert report.missing
        assert "cannot determine staleness" in report.missing[0]
        assert report.checked == 0

    def test_fresh_artifact_not_stale(self, tmp_path: Path):
        now = time.time()
        _touch(tmp_path / "design.yml", mtime=now - 100)
        _touch(tmp_path / "build_manifest.json", mtime=now)

        report = check_top_gen_staleness(tmp_path)

        assert report.checked == 1
        assert not report.has_stale
        assert report.stale_count == 0

    def test_stale_artifact_older_than_design_yml(self, tmp_path: Path):
        now = time.time()
        _touch(tmp_path / "build_manifest.json", mtime=now - 100)
        _touch(tmp_path / "design.yml", mtime=now)  # edited after artifact generated

        report = check_top_gen_staleness(tmp_path)

        assert report.has_stale
        assert report.stale_count == 1
        assert report.stale[0].artifact.name == "build_manifest.json"

    def test_missing_build_manifest_reported(self, tmp_path: Path):
        _touch(tmp_path / "design.yml")
        # No build_manifest.json written at all.

        report = check_top_gen_staleness(tmp_path)

        assert any("build_manifest.json" in m for m in report.missing)
        assert any("forge topgen gen-top" in m for m in report.missing)

    def test_missing_primary_hdl_output_not_reported(self, tmp_path: Path):
        # algo_top.v/.vhd absence is expected (language-specific) and must
        # not itself be flagged as missing.
        _touch(tmp_path / "design.yml")

        report = check_top_gen_staleness(tmp_path)

        assert not any("algo_top.v" in m or "algo_top.vhd" in m for m in report.missing)

    def test_explicit_design_yml_path_used(self, tmp_path: Path):
        now = time.time()
        other_dir = tmp_path / "elsewhere"
        design = _touch(other_dir / "design.yml", mtime=now)
        _touch(tmp_path / "build_manifest.json", mtime=now - 100)

        report = check_top_gen_staleness(tmp_path, design_yml=design)

        assert report.has_stale
        assert report.stale[0].source == design.resolve()

    def test_modules_yml_considered_as_source(self, tmp_path: Path):
        now = time.time()
        _touch(tmp_path / "design.yml", mtime=now - 200)
        _touch(tmp_path / "modules.yml", mtime=now)  # newest source
        _touch(tmp_path / "build_manifest.json", mtime=now - 100)

        report = check_top_gen_staleness(tmp_path)

        assert report.has_stale
        assert report.stale[0].source.name == "modules.yml"

    def test_extra_sources_considered(self, tmp_path: Path):
        now = time.time()
        _touch(tmp_path / "design.yml", mtime=now - 200)
        extra = _touch(tmp_path / "custom_contract.yml", mtime=now)
        _touch(tmp_path / "build_manifest.json", mtime=now - 100)

        report = check_top_gen_staleness(tmp_path, extra_sources=[extra])

        assert report.has_stale
        assert report.stale[0].source == extra


class TestCheckIpInfoStaleness:
    def test_missing_ip_info_reported(self, tmp_path: Path):
        report = check_ip_info_staleness(tmp_path / "ip_info.yaml")
        assert report.missing
        assert "forge topgen ip-summary" in report.missing[0]

    def test_fresh_ip_info_not_stale(self, tmp_path: Path):
        now = time.time()
        design = _touch(tmp_path / "design.yml", mtime=now - 100)
        ip_info = _touch(tmp_path / "ip_info.yaml", mtime=now)

        report = check_ip_info_staleness(ip_info, design_yml=design)

        assert not report.has_stale

    def test_stale_ip_info_older_than_design(self, tmp_path: Path):
        now = time.time()
        ip_info = _touch(tmp_path / "ip_info.yaml", mtime=now - 100)
        design = _touch(tmp_path / "design.yml", mtime=now)

        report = check_ip_info_staleness(ip_info, design_yml=design)

        assert report.has_stale

    def test_no_sources_no_findings(self, tmp_path: Path):
        ip_info = _touch(tmp_path / "ip_info.yaml")
        report = check_ip_info_staleness(ip_info)
        assert not report.has_stale
        assert report.checked == 0

    def test_ip_sources_dir_scanned_for_xci_and_zip(self, tmp_path: Path):
        now = time.time()
        ip_info = _touch(tmp_path / "ip_info.yaml", mtime=now - 100)
        ips_dir = tmp_path / "ips"
        _touch(ips_dir / "some_ip.xci", mtime=now)

        report = check_ip_info_staleness(ip_info, ip_sources_dir=ips_dir)

        assert report.has_stale
        assert report.stale[0].source.suffix == ".xci"


class TestContentHashAwareStaleness:
    """mtime stays the fast pre-check, but a sibling provenance.json
    (gen-top writes one into output_dir) can now confirm a source's
    content hasn't actually changed, overriding a misleading mtime bump."""

    def test_touched_but_unchanged_source_reports_fresh_with_provenance(self, tmp_path: Path):
        from forge.core.utils.content_hash import hash_file
        from forge.ir.provenance import ProvenanceManifest, write_provenance

        now = time.time()
        design = _touch(tmp_path / "design.yml", mtime=now - 100, content="unchanged")
        _touch(tmp_path / "build_manifest.json", mtime=now - 200)

        # A manifest recorded when design.yml had this exact content...
        write_provenance(
            tmp_path / "provenance.json",
            ProvenanceManifest(source_hashes={"design.yml": hash_file(design)}),
        )
        # ...then design.yml gets touched (mtime bumped) without its
        # content actually changing — e.g. a checkout, a `touch`.
        os.utime(design, (now, now))

        report = check_top_gen_staleness(tmp_path)

        assert report.checked == 1
        assert not report.has_stale, "content-hash confirmation should override the mtime bump"
        assert report.stale_count == 0

    def test_genuinely_changed_source_still_reports_stale_with_provenance(self, tmp_path: Path):
        """Regression guard: content-hash confirmation must never become
        falsely permissive — a real content change still reports stale
        even with a provenance.json present."""
        from forge.ir.provenance import ProvenanceManifest, write_provenance

        now = time.time()
        design = _touch(tmp_path / "design.yml", mtime=now - 100, content="v1")
        _touch(tmp_path / "build_manifest.json", mtime=now - 200)

        # Manifest recorded a hash for v1's content...
        write_provenance(
            tmp_path / "provenance.json",
            ProvenanceManifest(source_hashes={"design.yml": "hash-of-v1-content"}),
        )
        # ...then design.yml is genuinely edited (real content change).
        _touch(design, mtime=now, content="v2 - a real edit")

        report = check_top_gen_staleness(tmp_path)

        assert report.has_stale
        assert report.stale_count == 1
        assert report.stale[0].content_confirmed_fresh is False

    def test_no_provenance_json_falls_back_to_mtime_only(self, tmp_path: Path):
        """Backward compatible: a project that hasn't regenerated since
        this feature landed (no provenance.json at all) falls back to
        mtime-only staleness checking."""
        now = time.time()
        _touch(tmp_path / "build_manifest.json", mtime=now - 100)
        _touch(tmp_path / "design.yml", mtime=now)

        report = check_top_gen_staleness(tmp_path)

        assert report.has_stale
        assert report.stale[0].content_confirmed_fresh is None


class TestFormatStaleReport:
    def test_empty_report_produces_no_lines(self, tmp_path: Path):
        report = check_top_gen_staleness(tmp_path)
        # No sources at all -> only a "missing" line, no stale entries.
        lines = format_stale_report(report)
        assert all("[ATG016]" in ln for ln in lines)

    def test_stale_finding_produces_two_lines(self, tmp_path: Path):
        now = time.time()
        _touch(tmp_path / "build_manifest.json", mtime=now - 100)
        _touch(tmp_path / "design.yml", mtime=now)
        report = check_top_gen_staleness(tmp_path)

        lines = format_stale_report(report)

        assert any("is stale" in ln for ln in lines)
        assert any("Re-run: forge topgen gen-top" in ln for ln in lines)
