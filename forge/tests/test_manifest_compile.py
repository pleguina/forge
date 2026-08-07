"""Tests for forge.verification.manifest_compile — builds compile.prj (and
include_dirs.txt / dat_files.txt) from a build_manifest.json for the xsim
backend. This is real logic exercised by every xsim run this session.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.verification.manifest_compile import prepare_from_manifest


def _write_manifest(tmp_path: Path, **fields) -> Path:
    manifest = {
        "verilog_files": [],
        "systemverilog_files": [],
        "verilog_include_files": [],
        "include_dirs": [],
        **fields,
    }
    p = tmp_path / "build_manifest.json"
    p.write_text(json.dumps(manifest))
    return p


def _make_source(tmp_path: Path, name: str, content: str = "module m; endmodule\n") -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


@pytest.fixture()
def base_dirs(tmp_path: Path):
    stim = tmp_path / "stim"
    bindings = tmp_path / "bindings"
    work = tmp_path / "work"
    stim.mkdir()
    bindings.mkdir()
    return stim, bindings, work


class TestPrepareFromManifestErrors:
    def test_missing_manifest_raises_file_not_found(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        tb = _make_source(tmp_path, "tb.sv")
        with pytest.raises(FileNotFoundError, match="Build manifest not found"):
            prepare_from_manifest(
                manifest_path=tmp_path / "does_not_exist.json",
                tb_file=tb, extra_sources=(), stim_dir=stim,
                bindings_dir=bindings, work_dir=work,
            )

    def test_missing_testbench_raises_file_not_found(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        manifest = _write_manifest(tmp_path)
        with pytest.raises(FileNotFoundError, match="Testbench not found"):
            prepare_from_manifest(
                manifest_path=manifest, tb_file=tmp_path / "missing_tb.sv",
                extra_sources=(), stim_dir=stim, bindings_dir=bindings, work_dir=work,
            )

    def test_missing_manifest_source_raises_value_error(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, verilog_files=[str(tmp_path / "nope.v")])
        with pytest.raises(ValueError, match="Manifest source not found on disk"):
            prepare_from_manifest(
                manifest_path=manifest, tb_file=tb, extra_sources=(),
                stim_dir=stim, bindings_dir=bindings, work_dir=work,
            )

    def test_missing_extra_source_raises_file_not_found(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path)
        with pytest.raises(FileNotFoundError, match="Extra RTL source not found"):
            prepare_from_manifest(
                manifest_path=manifest, tb_file=tb,
                extra_sources=[(tmp_path / "glbl.v", "verilog")],
                stim_dir=stim, bindings_dir=bindings, work_dir=work,
            )


class TestPrepareFromManifestHappyPath:
    def test_writes_compile_prj_include_dirs_and_dat_files(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        v = _make_source(tmp_path, "mod.v")
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, verilog_files=[str(v)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        assert (work / "compile.prj").exists()
        assert (work / "include_dirs.txt").exists()
        assert (work / "dat_files.txt").exists()
        assert any("mod.v" in e for e in result["entries"])
        assert any("tb.sv" in e for e in result["entries"])

    def test_testbench_is_last_entry(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        v = _make_source(tmp_path, "mod.v")
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, verilog_files=[str(v)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        assert "tb.sv" in result["entries"][-1]

    def test_verilog_file_tagged_verilog_lang(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        v = _make_source(tmp_path, "mod.v")
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, verilog_files=[str(v)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        mod_entry = next(e for e in result["entries"] if "mod.v" in e)
        assert mod_entry.startswith("verilog ")

    def test_systemverilog_file_tagged_sv_lang(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        sv = _make_source(tmp_path, "mod.sv")
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, systemverilog_files=[str(sv)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        mod_entry = next(e for e in result["entries"] if "mod.sv" in e)
        assert mod_entry.startswith("sv ")

    def test_duplicate_source_not_added_twice(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        v = _make_source(tmp_path, "mod.v")
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, verilog_files=[str(v), str(v)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        assert sum("mod.v" in e for e in result["entries"]) == 1

    def test_excluded_files_are_skipped(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        v = _make_source(tmp_path, "mod.v")
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, verilog_files=[str(v)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
            excluded_files={v},
        )

        assert not any("mod.v" in e for e in result["entries"])

    def test_include_only_suffix_becomes_include_dir_not_entry(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        svh_dir = tmp_path / "inc"
        svh_dir.mkdir()
        svh = _make_source(svh_dir, "defs.svh", content="`define X 1\n")
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path, verilog_include_files=[str(svh)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        assert not any("defs.svh" in e for e in result["entries"])
        assert any(str(svh_dir.resolve()) == d for d in result["include_dirs"])

    def test_extra_sources_appended(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        tb = _make_source(tmp_path, "tb.sv")
        glbl = _make_source(tmp_path, "glbl.v")
        manifest = _write_manifest(tmp_path)

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb,
            extra_sources=[(glbl, "verilog")],
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        assert any("glbl.v" in e for e in result["entries"])

    def test_stim_and_bindings_dirs_added_as_include_dirs(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        tb = _make_source(tmp_path, "tb.sv")
        manifest = _write_manifest(tmp_path)

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        assert str(stim.resolve()) in result["include_dirs"]
        assert str(bindings.resolve()) in result["include_dirs"]

    def test_dat_files_staged_when_requested(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        tb = _make_source(tmp_path, "tb.sv")
        rom_dir = tmp_path / "rom"
        rom_dir.mkdir()
        (rom_dir / "table.dat").write_text("0000\n")
        manifest = _write_manifest(tmp_path, include_dirs=[str(rom_dir)])

        result = prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
            stage_dat=True,
        )

        assert len(result["dat_files"]) == 1
        assert (work / "table.dat").exists()

    def test_dat_files_not_staged_by_default(self, tmp_path: Path, base_dirs):
        stim, bindings, work = base_dirs
        tb = _make_source(tmp_path, "tb.sv")
        rom_dir = tmp_path / "rom"
        rom_dir.mkdir()
        (rom_dir / "table.dat").write_text("0000\n")
        manifest = _write_manifest(tmp_path, include_dirs=[str(rom_dir)])

        prepare_from_manifest(
            manifest_path=manifest, tb_file=tb, extra_sources=(),
            stim_dir=stim, bindings_dir=bindings, work_dir=work,
        )

        assert not (work / "table.dat").exists()
