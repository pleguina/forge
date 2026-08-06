"""forge.core.stale_detection — Stale artifact detection for top generation (B4).

Detects when top-generation outputs are older than their source contracts,
preventing silent mismatches caused by editing ``design.yml``, ``modules.yml``,
or IP packages without re-running ``forge topgen gen-top``.

Artifacts checked per generation run
--------------------------------------
  * ``algo_top.v`` / ``algo_top.vhd``  — primary HDL output
  * ``build_manifest.json``            — build manifest (connection, wiring stats)
  * ``maturity_report.json``           — machine-readable release-readiness gate
  * ``port_map.yaml``                  — port extraction result
  * ``port_signature.json``            — SHA hash of generated port list
  * ``design_parameters.json``         — timing / interface parameters

Sources that trigger staleness
--------------------------------
  * ``design.yml``        — primary design contract (always checked)
  * ``modules.yml``       — IP registry contract (when present)
  * ``ip_info.yaml``      — generated IP metadata (compared against its own inputs)
  * All ``.xci`` / ``.zip`` / ``.yml`` IP source files referenced in ``ip-summary``

Public API
----------
  ArtifactStaleness     dataclass — one stale finding
  StaleTopGenReport     dataclass — full report for one generation root
  check_top_gen_staleness(output_dir, design_yml, modules_yml, ip_info_yaml)
      → StaleTopGenReport
  format_stale_report(report) → list[str]
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from forge.core.provenance_staleness import confirms_fresh, describe_staleness_basis


# ── Helpers ────────────────────────────────────────────────────────────────

def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return float("-inf")


def _fmt_time(ts: float) -> str:
    if ts == float("-inf"):
        return "(missing)"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ── Result model ───────────────────────────────────────────────────────────

@dataclass
class ArtifactStaleness:
    """A single stale-artifact finding.

    Attributes:
        artifact:       The generated artifact path.
        source:         The source file that is newer than *artifact*.
        artifact_mtime: Modification time of the artifact.
        source_mtime:   Modification time of the source.
        content_confirmed_fresh: ``True`` when a
            provenance manifest confirms *source*'s content hasn't
            actually changed despite a newer mtime (overrides the
            mtime-only verdict below to "not stale"). ``None`` (the
            default) means no usable provenance data was found — mtime
            alone decides, exactly as before this check existed.
    """
    artifact:       Path
    source:         Path
    artifact_mtime: float
    source_mtime:   float
    content_confirmed_fresh: Optional[bool] = None

    @property
    def stale(self) -> bool:
        if self.content_confirmed_fresh:
            return False
        return self.source_mtime > self.artifact_mtime

    def message(self) -> str:
        return (
            f"{self.artifact.name} is stale\n"
            f"    artifact: {self.artifact}  [{_fmt_time(self.artifact_mtime)}]\n"
            f"    newer source: {self.source.name}  [{_fmt_time(self.source_mtime)}]\n"
            f"    reason: {describe_staleness_basis(self.content_confirmed_fresh)}"
        )


@dataclass
class StaleTopGenReport:
    """Full staleness report for one top-generation root."""

    output_dir:   Path
    stale:        list[ArtifactStaleness] = field(default_factory=list)
    missing:      list[str]              = field(default_factory=list)   # expected artifacts not present
    checked:      int                    = 0    # number of (artifact, source) pairs checked

    @property
    def has_stale(self) -> bool:
        return bool(self.stale)

    @property
    def stale_count(self) -> int:
        return len(self.stale)


# ── Standard artifact names ────────────────────────────────────────────────

_GENERATED_ARTIFACTS = (
    "algo_top.v",
    "algo_top.vhd",
    "build_manifest.json",
    "maturity_report.json",
    "port_map.yaml",
    "port_signature.json",
    "design_parameters.json",
)


# ── Core check logic ───────────────────────────────────────────────────────

def check_top_gen_staleness(
    output_dir: "str | Path",
    design_yml: "str | Path | None" = None,
    modules_yml: "str | Path | None" = None,
    ip_info_yaml: "str | Path | None" = None,
    *,
    extra_sources: "list[Path] | None" = None,
) -> StaleTopGenReport:
    """Check whether top-generation outputs are stale relative to source contracts.

    Args:
        output_dir:   Directory where ``algo_top.v``, ``build_manifest.json``,
                      etc. are written (typically the project root or ``output/``).
        design_yml:   Path to ``design.yml`` (optional — auto-discovered if absent).
        modules_yml:  Path to ``modules.yml`` (optional).
        ip_info_yaml: Path to ``ip_info.yaml`` (optional — auto-discovered).
        extra_sources: Additional source files to include in the staleness check.

    Returns:
        :class:`StaleTopGenReport` listing stale artifacts and missing files.
    """
    output_dir = Path(output_dir).resolve()
    report = StaleTopGenReport(output_dir=output_dir)

    # ── Auto-discover sources if not given ─────────────────────────────────
    sources: list[Path] = list(extra_sources or [])

    # design.yml — search up from output_dir
    if design_yml is not None:
        dyl = Path(design_yml)
        if dyl.exists():
            sources.append(dyl)
    else:
        for candidate in (
            output_dir / "design.yml",
            output_dir.parent / "design.yml",
        ):
            if candidate.exists():
                sources.append(candidate)
                break

    # modules.yml
    if modules_yml is not None:
        myl = Path(modules_yml)
        if myl.exists():
            sources.append(myl)
    else:
        for candidate in (
            output_dir / "modules.yml",
            output_dir.parent / "modules.yml",
        ):
            if candidate.exists():
                sources.append(candidate)
                break

    # ip_info.yaml
    if ip_info_yaml is not None:
        iyl = Path(ip_info_yaml)
        if iyl.exists():
            sources.append(iyl)
    else:
        for candidate in (
            output_dir / "ip_info.yaml",
            output_dir.parent / "ip_info.yaml",
        ):
            if candidate.exists():
                sources.append(candidate)
                break

    if not sources:
        report.missing.append(
            "No source files (design.yml / modules.yml / ip_info.yaml) found "
            f"under {output_dir} — cannot determine staleness."
        )
        return report

    # Determine the newest source and which file it is
    def _newest(paths: list[Path]) -> tuple[Path, float]:
        best_path = paths[0]
        best_mtime = float("-inf")
        for p in paths:
            m = _mtime(p)
            if m > best_mtime:
                best_mtime = m
                best_path = p
        return best_path, best_mtime

    # ── Check each generated artifact ─────────────────────────────────────
    for artifact_name in _GENERATED_ARTIFACTS:
        artifact_path = output_dir / artifact_name
        if not artifact_path.exists():
            # Only flag missing for the primary HDL outputs — others are optional
            if artifact_name in ("algo_top.v", "algo_top.vhd"):
                continue  # language-specific — only one will exist
            if artifact_name == "build_manifest.json":
                report.missing.append(
                    f"{artifact_name}: not found at {artifact_path}  "
                    f"→ run forge topgen gen-top to generate"
                )
            continue

        artifact_mtime = _mtime(artifact_path)
        newest_src, newest_mtime = _newest(sources)

        report.checked += 1
        finding = ArtifactStaleness(
            artifact=artifact_path,
            source=newest_src,
            artifact_mtime=artifact_mtime,
            source_mtime=newest_mtime,
        )
        if finding.stale:
            # mtime says stale — confirm against a
            # provenance.json (gen-top writes one into output_dir)
            # before trusting it. A touched-but-unchanged
            # source no longer reports as stale.
            finding.content_confirmed_fresh = confirms_fresh(newest_src, output_dir)
        if finding.stale:
            report.stale.append(finding)

    return report


def check_ip_info_staleness(
    ip_info_yaml: "str | Path",
    design_yml: "str | Path | None" = None,
    ip_sources_dir: "str | Path | None" = None,
) -> StaleTopGenReport:
    """Check whether ``ip_info.yaml`` is stale relative to IP source files.

    ``ip_info.yaml`` is generated by ``forge topgen ip-summary``.  It becomes
    stale when IP packages or the design contract change.

    Args:
        ip_info_yaml:   Path to the ``ip_info.yaml`` file.
        design_yml:     Path to ``design.yml`` (source contract for the scan).
        ip_sources_dir: Root directory to search for ``.xci``, ``.zip``, and
                        ``.yml`` IP source files.

    Returns:
        :class:`StaleTopGenReport`.
    """
    ip_info_yaml = Path(ip_info_yaml).resolve()
    report = StaleTopGenReport(output_dir=ip_info_yaml.parent)

    if not ip_info_yaml.exists():
        report.missing.append(
            f"ip_info.yaml not found: {ip_info_yaml}  "
            f"→ run: forge topgen ip-summary"
        )
        return report

    ip_mtime = _mtime(ip_info_yaml)
    sources: list[Path] = []

    # design.yml
    if design_yml is not None and Path(design_yml).exists():
        sources.append(Path(design_yml))

    # IP source files
    if ip_sources_dir is not None:
        root = Path(ip_sources_dir)
        for pattern in ("**/*.xci", "**/*.zip"):
            sources.extend(root.glob(pattern))

    if not sources:
        return report

    def _newest(paths: list[Path]) -> tuple[Path, float]:
        best_path, best_mtime = paths[0], float("-inf")
        for p in paths:
            m = _mtime(p)
            if m > best_mtime:
                best_mtime, best_path = m, p
        return best_path, best_mtime

    newest_src, newest_mtime = _newest(sources)
    report.checked += 1
    finding = ArtifactStaleness(
        artifact=ip_info_yaml,
        source=newest_src,
        artifact_mtime=ip_mtime,
        source_mtime=newest_mtime,
    )
    if finding.stale:
        finding.content_confirmed_fresh = confirms_fresh(newest_src, ip_info_yaml.parent)
    if finding.stale:
        report.stale.append(finding)

    return report


def format_stale_report(report: StaleTopGenReport) -> list[str]:
    """Return a list of human-readable warning lines from *report*."""
    lines: list[str] = []
    for s in report.stale:
        lines.append(f"[ATG016] {s.message()}")
        lines.append(f"         → Re-run: forge topgen gen-top")
    for m in report.missing:
        lines.append(f"[ATG016] {m}")
    return lines
