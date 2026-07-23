#!/usr/bin/env python3
"""Framework utility — build a compile.prj from a ``build_manifest.json``.

:func:`prepare_from_manifest` is called by :class:`fw_verify.backend_xsim.XsimBackend`
when a plugin's :meth:`build_manifest_path` hook returns a non-None path.

It handles:
  * Reading ``build_manifest.json`` produced by ``topgen`` (or any
    compatible multi-IP build system).
  * Filtering source files by HDL language and generating per-file
    ``sv``/``verilog`` entries in ``compile.prj``.
  * Collecting include directories and writing ``include_dirs.txt``.
  * Discovering and optionally staging ``.dat`` ROM files to the work dir.
  * Appending optional extra sources (``glbl.v``, IP stubs, …) supplied by
    the plugin via :meth:`extra_rtl_sources`.

``compile.prj``, ``include_dirs.txt``, and ``dat_files.txt`` are written to
``work_dir``.  ``xvlog`` reads ``include_dirs.txt`` indirectly; the backend
passes each dir as ``--include``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

# ── Source filtering ───────────────────────────────────────────────────────

_INCLUDE_ONLY_SUFFIXES = frozenset({".vh", ".svh"})
_VERILOG_SUFFIXES      = frozenset({".v"})
_SV_SUFFIXES           = frozenset({".sv"})
_HDL_SUFFIXES          = _VERILOG_SUFFIXES | _SV_SUFFIXES | _INCLUDE_ONLY_SUFFIXES


def _lang(path: Path) -> str:
    return "sv" if path.suffix.lower() in _SV_SUFFIXES else "verilog"


# ── Public API ─────────────────────────────────────────────────────────────

def prepare_from_manifest(
    *,
    manifest_path:  Path,
    tb_file:        Path,
    extra_sources:  "list[tuple[Path, str]]" = (),
    stim_dir:       Path,
    bindings_dir:   Path,
    work_dir:       Path,
    excluded_files: "set[Path] | None" = None,
    stage_dat:      bool = False,
) -> dict[str, list[str]]:
    """Build ``compile.prj`` and auxiliary files in *work_dir* from a manifest.

    Parameters
    ----------
    manifest_path:
        Absolute path to ``build_manifest.json``.
    tb_file:
        Absolute path to the testbench ``.sv`` file (appended last).
    extra_sources:
        ``[(path, lang), ...]`` entries appended after manifest sources and
        before the testbench.  Typically ``glbl.v`` and floating-point stubs.
        ``lang`` must be ``"sv"`` or ``"verilog"``.
    stim_dir:
        Directory containing stimulus ``.svh`` files (added as include dir).
    bindings_dir:
        Directory containing ``tb_bindings.svh`` (added as include dir).
    work_dir:
        Output directory; receives ``compile.prj``, ``include_dirs.txt``,
        ``dat_files.txt``.
    excluded_files:
        Set of absolute :class:`Path` objects to skip.
    stage_dat:
        If ``True``, copy all discovered ``.dat`` files flat into *work_dir*.

    Returns
    -------
    dict with keys ``"entries"``, ``"include_dirs"``, ``"dat_files"``
    (lists of ``str``).

    Raises
    ------
    FileNotFoundError
        Manifest or a required extra source does not exist.
    ValueError
        A source listed in the manifest is not found on disk.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(f"Build manifest not found: {manifest_path}")

    manifest: dict = json.loads(manifest_path.read_text())
    excluded: set[str] = {str(p.resolve()) for p in (excluded_files or set())}

    entries:      list[str] = []
    include_dirs: list[str] = []
    dat_files:    list[str] = []
    seen_entries: set[str]  = set()
    seen_dirs:    set[str]  = set()
    seen_dat:     set[str]  = set()

    def _add_include_dir(p: Path) -> None:
        norm = str(p.resolve())
        if norm not in seen_dirs and p.is_dir():
            seen_dirs.add(norm)
            include_dirs.append(norm)

    def _add_dat_dir(d: Path) -> None:
        if not d.is_dir():
            return
        for dat in sorted(d.glob("*.dat")):
            r = str(dat.resolve())
            if r not in seen_dat:
                seen_dat.add(r)
                dat_files.append(r)

    def _add_file(path_str: str, lang: str | None = None) -> None:
        path = Path(path_str).resolve()
        norm = str(path)
        if norm in excluded or norm in seen_entries:
            return
        suffix = path.suffix.lower()
        if suffix not in _HDL_SUFFIXES:
            return
        if suffix in _INCLUDE_ONLY_SUFFIXES:
            _add_include_dir(path.parent)
            _add_dat_dir(path.parent)
            return
        if not path.exists():
            raise ValueError(f"Manifest source not found on disk: {path}")
        seen_entries.add(norm)
        resolved_lang = lang or _lang(path)
        entries.append(f'{resolved_lang} xil_defaultlib "{path}"')
        _add_include_dir(path.parent)
        _add_dat_dir(path.parent)

    # ── Manifest include dirs ──────────────────────────────────────────────
    for d in manifest.get("include_dirs", []):
        dp = Path(d).resolve()
        _add_include_dir(dp)
        _add_dat_dir(dp)

    # ── Manifest source files ──────────────────────────────────────────────
    for f in manifest.get("verilog_files", []):
        _add_file(f)
    for f in manifest.get("systemverilog_files", []):
        _add_file(f, "sv")
    for f in manifest.get("verilog_include_files", []):
        _add_file(f)

    # ── HLS module verilog from hls_build_root ─────────────────────────────
    # Expand HLS modules whose verilog_files are empty (not yet resolved at
    # topgen time). Most workspaces key build_hls/ by canonical module name,
    # while some older layouts key it by HLS top name, so try both.
    hls_build_root_str = manifest.get("hls_build_root", "")
    if hls_build_root_str:
        hls_root = Path(hls_build_root_str)
        for mod_key, mod_info in manifest.get("modules", {}).items():
            if mod_info.get("kind") != "hls":
                continue
            if mod_info.get("verilog_files"):
                continue
            mod_name = mod_info.get("name", mod_key)
            top = mod_info.get("top", "")
            if not top:
                continue
            candidates = [
                hls_root / mod_name / "solution1" / "syn" / "verilog" / f"{top}.v",
            ]
            if mod_name != top:
                candidates.append(
                    hls_root / top / "solution1" / "syn" / "verilog" / f"{top}.v"
                )

            for hls_v in candidates:
                if hls_v.exists():
                    # Add the top .v and all sibling sub-function .v files
                    # that HLS generates in the same directory.
                    _add_file(str(hls_v))
                    for sibling in sorted(hls_v.parent.glob("*.v")):
                        if sibling != hls_v:
                            _add_file(str(sibling))
                    break
            else:
                import warnings
                warnings.warn(
                    f"HLS verilog not found for module '{mod_name}' (top '{top}'):\n"
                    + "\n".join(f"  - {candidate}" for candidate in candidates)
                    + "\n"
                    f"  Run 'forge hls run --stages synth' first.",
                    stacklevel=2,
                )

    # ── Extra sources (glbl.v, fp stubs, …) ───────────────────────────────
    for extra_path, lang in extra_sources:
        if extra_path is not None:
            extra_path = Path(extra_path)
            if not extra_path.exists():
                raise FileNotFoundError(f"Extra RTL source not found: {extra_path}")
            _add_file(str(extra_path), lang)

    # ── Testbench (always last) ────────────────────────────────────────────
    if not tb_file.exists():
        raise FileNotFoundError(f"Testbench not found: {tb_file}")
    tb_norm = str(tb_file.resolve())
    if tb_norm not in seen_entries:
        seen_entries.add(tb_norm)
        entries.append(f'sv xil_defaultlib "{tb_file.resolve()}"')
    _add_include_dir(tb_file.parent)

    # ── Stimulus and bindings include dirs (added last) ────────────────────
    for d in (stim_dir, bindings_dir):
        p = d.resolve() if d.exists() else d
        _add_include_dir(p)

    # ── Write outputs ──────────────────────────────────────────────────────
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "compile.prj").write_text("\n".join(entries) + "\n")
    (work_dir / "include_dirs.txt").write_text("\n".join(include_dirs) + "\n")
    (work_dir / "dat_files.txt").write_text("\n".join(dat_files) + "\n")

    if stage_dat and dat_files:
        for dat_str in dat_files:
            src = Path(dat_str)
            dst = work_dir / src.name
            if src != dst:
                shutil.copy2(src, dst)
        print(f"[prepare] Staged {len(dat_files)} .dat file(s) into {work_dir}")

    print(
        f"[prepare] compile.prj: {len(entries)} source entries, "
        f"{len(include_dirs)} include dirs, {len(dat_files)} .dat files"
    )
    return {
        "entries":      entries,
        "include_dirs": include_dirs,
        "dat_files":    dat_files,
    }
