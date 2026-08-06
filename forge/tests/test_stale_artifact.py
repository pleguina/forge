"""
Tests for forge.verify.stale_artifact — verify-flow artifact staleness,
including content-hash-aware confirmation layered on top of the existing
mtime-only checks.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from forge.ir.provenance import ProvenanceManifest, write_provenance
from forge.verify.stale_artifact import (
    StalenessResult,
    StaleArtifactReport,
    _check,
)


def _touch(path: Path, *, mtime: float | None = None, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestStalenessResult:
    def test_stale_when_source_newer(self):
        result = StalenessResult(
            artifact_path=Path("port_map.yaml"), source_path=Path("algo_top.v"),
            artifact_mtime=100.0, source_mtime=200.0,
        )
        assert result.stale is True

    def test_content_confirmed_fresh_overrides_mtime_verdict(self):
        result = StalenessResult(
            artifact_path=Path("port_map.yaml"), source_path=Path("algo_top.v"),
            artifact_mtime=100.0, source_mtime=200.0,
            content_confirmed_fresh=True,
        )
        assert result.stale is False

    def test_content_confirmed_not_fresh_keeps_stale_verdict(self):
        result = StalenessResult(
            artifact_path=Path("port_map.yaml"), source_path=Path("algo_top.v"),
            artifact_mtime=100.0, source_mtime=200.0,
            content_confirmed_fresh=False,
        )
        assert result.stale is True


class TestCheckContentHashAware:
    """Direct tests of the module-private `_check` helper `check_flow_staleness`
    delegates every artifact comparison to — real for the common
    `dut_rtl_source: gen-top/<name>` layout, where the DUT RTL file lives
    right next to gen-top's own provenance.json."""

    def test_touched_but_unchanged_rtl_reports_fresh_with_provenance(self, tmp_path: Path):
        from forge.core.utils.content_hash import hash_file

        now = time.time()
        rtl_dir = tmp_path / "gen-top" / "design_x"
        rtl = _touch(rtl_dir / "algo_top.v", mtime=now - 100, content="module algo_top; endmodule")
        port_map = _touch(tmp_path / "port_map.yaml", mtime=now - 200)

        write_provenance(
            rtl_dir / "provenance.json",
            ProvenanceManifest(output_hashes={"algo_top.v": hash_file(rtl)}),
        )
        os.utime(rtl, (now, now))  # touched, content unchanged

        report = StaleArtifactReport(flow_name="algo_top_xsim")
        _check(report, port_map, rtl, now)

        # A confirmed-fresh finding is never appended at all (_check only
        # records genuinely-stale results) — the observable proof is that
        # nothing landed in the report despite the newer mtime.
        assert not report.has_stale
        assert report.stale_items == []

    def test_genuinely_changed_rtl_still_reports_stale(self, tmp_path: Path):
        now = time.time()
        rtl_dir = tmp_path / "gen-top" / "design_x"
        rtl = _touch(rtl_dir / "algo_top.v", mtime=now - 100, content="module algo_top; endmodule")
        port_map = _touch(tmp_path / "port_map.yaml", mtime=now - 200)

        write_provenance(
            rtl_dir / "provenance.json",
            ProvenanceManifest(output_hashes={"algo_top.v": "hash-of-a-different-version"}),
        )
        os.utime(rtl, (now, now))

        report = StaleArtifactReport(flow_name="algo_top_xsim")
        _check(report, port_map, rtl, now)

        assert report.has_stale
        assert report.stale_items[0].content_confirmed_fresh is False

    def test_no_provenance_json_falls_back_to_mtime_only(self, tmp_path: Path):
        """The common case for HLS-synthesis-sourced flows
        (`dut_rtl_source: build_hls_.../syn/verilog`) — no provenance.json
        exists there, so behavior falls back to mtime-only staleness."""
        now = time.time()
        rtl = _touch(tmp_path / "hit_decoder.v", mtime=now)
        port_map = _touch(tmp_path / "port_map.yaml", mtime=now - 100)

        report = StaleArtifactReport(flow_name="hit_decoder_xsim")
        _check(report, port_map, rtl, now)

        assert report.has_stale
        assert report.stale_items[0].content_confirmed_fresh is None
