"""How a ``forge.yml`` project is verified, and what its last ``forge test`` found.

``forge check``, ``forge test`` and ``forge report`` all answer these two
questions; they read them from here so that a project configured one way is
not told by one command that it is configured another, and results one
command wrote are the results the next one shows.

A project verifies either through a plugin flow (``verification.config``,
a ``design.verification.yml``) or directly from data (``verification.input``
/ ``expected``, and the same per module under ``verification.modules``),
which ``forge test`` plans from the design itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

FLOWS = "flows"
DATA = "data"
DATASET = "dataset"
NONE = "none"

#: ``forge test`` statuses that verified nothing, and why.
UNVERIFIED = ("NOT_VERIFIED_NO_ORACLE", "BLOCKED_MISSING_TOOL", "PARTIAL", "SKIPPED")


@dataclass
class VerificationSetup:
    """What ``forge.yml`` configures for verification.

    ``data_files`` are ``(key, path)`` for every input/expected file named,
    so a check can report the ones that are missing; ``expected`` is whether
    any level has an expected output to compare against.
    """

    kind: str
    config: Optional[Path] = None
    data_files: List[Tuple[str, Path]] = field(default_factory=list)
    expected: bool = False
    backends: List[str] = field(default_factory=list)

    @property
    def missing(self) -> List[Tuple[str, Path]]:
        return [(key, path) for key, path in self.data_files if not path.is_file()]


def _data_path(value: Any, root: Path) -> Optional[Path]:
    if not value:
        return None
    raw = value.get("path") if isinstance(value, dict) else value
    return (root / str(raw)).resolve() if raw else None


def verification_setup(config: Any) -> VerificationSetup:
    """The :class:`VerificationSetup` of a loaded :class:`~forge.project.config.ForgeConfig`."""
    root: Path = config.root
    spec: Dict[str, Any] = dict(config.verification_spec or {})
    backends = [str(b) for b in spec.get("backends") or ["xsim", "verilator"]]
    if config.verification_config:
        return VerificationSetup(FLOWS, config=(root / config.verification_config).resolve(),
                                 backends=backends)
    files: List[Tuple[str, Path]] = []
    expected = False
    for key in ("input", "expected"):
        path = _data_path(spec.get(key), root)
        if path is not None:
            files.append((f"verification.{key}", path))
            expected = expected or key == "expected"
    for name, mod in sorted((spec.get("modules") or {}).items()):
        for key in ("input", "expected"):
            path = _data_path((mod or {}).get(key), root)
            if path is not None:
                files.append((f"verification.modules.{name}.{key}", path))
                expected = expected or key == "expected"
    if files:
        return VerificationSetup(DATA, data_files=files, expected=expected, backends=backends)
    if config.dataset:
        return VerificationSetup(DATASET, data_files=[("verification.dataset", (root / config.dataset).resolve())],
                                 backends=backends)
    return VerificationSetup(NONE, backends=backends)


# ── Results ───────────────────────────────────────────────────────────────

@dataclass
class ResultRecord:
    """One result ``forge test`` left under the project's report directory.

    ``stale`` names what changed since the result was recorded (a source, the
    input, the expected output) and is empty when nothing did or when the
    result does not record what it was computed from.
    """

    name: str
    status: str
    path: Path
    reason: str = ""
    stale: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.status in ("VERIFIED", "PASS") and not self.stale

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "status": self.status, "path": str(self.path),
                "reason": self.reason, "stale": self.stale}


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _evidence_staleness(doc: Dict[str, Any], plans: Optional[Dict[str, str]] = None) -> str:
    """What changed since *doc* was recorded: a source, a header, the data, or
    — when *plans* (level -> :func:`forge.verification.auto.plan_identity`) is
    given — the level's verification settings."""
    changed: List[str] = []
    for src in [*(doc.get("sources") or []), *(doc.get("headers") or [])]:
        if _sha256(Path(src["path"])) != src.get("sha256"):
            changed.append(Path(src["path"]).name)
    for key in ("input", "expected"):
        ref = doc.get(key)
        if ref and _sha256(Path(ref["path"])) != ref.get("sha256"):
            changed.append(f"the {key} {Path(ref['path']).name}")
    if plans is not None:
        level = str(doc.get("level", ""))
        if level not in plans:
            changed.append("the level is no longer in the verification plan")
        elif doc.get("plan_sha256") != plans[level]:
            changed.append("its verification settings")
    return ", ".join(changed)


def current_plans(config: Any) -> Optional[Dict[str, str]]:
    """level -> plan identity of the data-driven plan for a loaded ``forge.yml``
    and its last build, or None when no plan can be made (nothing built yet).
    Reads only; nothing under ``.forge/`` is rewritten."""
    from types import SimpleNamespace

    from forge.verification import auto

    paths = config.paths()
    project = SimpleNamespace(root=config.root, config=config, top_level=lambda: paths.top_level("verilog", "algo_top"))
    try:
        plan = auto.plan(project, spec=dict(config.verification_spec))
    except Exception:  # noqa: BLE001 — applicability then rests on file hashes alone
        return None
    return {level.name: auto.plan_identity(level) for level in plan.levels}


def evidence_dir(report_root: Path) -> Path:
    """Where ``forge test`` writes the evidence of its latest data-driven run."""
    return report_root / "test" / "auto"


def runs_dir(report_root: Path) -> Path:
    """Where every data-driven run keeps its own evidence, never rewritten."""
    return report_root / "test" / "runs"


def run_history(report_root: Path) -> List[Dict[str, Any]]:
    """``[{run, path, status}]`` of every recorded run, oldest first."""
    out: List[Dict[str, Any]] = []
    for run in sorted(p for p in runs_dir(report_root).glob("*") if p.is_dir()):
        summary = run / "run.json"
        try:
            doc = json.loads(summary.read_text())
        except (OSError, ValueError):
            continue
        out.append({"run": run.name, "path": str(run), "status": doc.get("verification")})
    return out


def latest_results(report_root: Path, plans: Optional[Dict[str, str]] = None) -> List[ResultRecord]:
    """Every result under *report_root*: data-driven evidence and flow results.

    *plans* (see :func:`current_plans`) also marks a result out of date when
    its level's verification settings changed or the level was dropped.
    """
    records: List[ResultRecord] = []
    for path in sorted(evidence_dir(report_root).glob("*.evidence.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        records.append(ResultRecord(
            name=f"{doc.get('level', path.stem)}[{doc.get('backend', '?')}]",
            status=str(doc.get("status", "UNKNOWN")),
            path=path,
            reason=str(doc.get("reason", "")),
            stale=_evidence_staleness(doc, plans),
            payload=doc,
        ))
    for path in sorted((report_root / "test").glob("*.results.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        records.append(ResultRecord(
            name=str(doc.get("flow_name") or path.name[: -len(".results.json")]),
            status="PASS" if doc.get("success") else "FAIL",
            path=path,
            payload=doc,
        ))
    return records


def overall_status(records: Iterable[ResultRecord]) -> str:
    """One status for *records*: FAILED beats OUT_OF_DATE beats anything
    unverified beats VERIFIED. A failure is reported even when out of date;
    a pass that no longer applies is not."""
    records = list(records)
    if any(r.stale for r in records) and not any(
            r.status in ("FAILED", "FAIL") for r in records):
        return "OUT_OF_DATE"
    statuses = [r.status for r in records]
    if not statuses:
        return "NONE"
    if len(set(statuses)) == 1:
        return {"PASS": "VERIFIED", "FAIL": "FAILED"}.get(statuses[0], statuses[0])
    if any(s in ("FAILED", "FAIL") for s in statuses):
        return "FAILED"
    if all(s in ("VERIFIED", "PASS") for s in statuses):
        return "VERIFIED"
    return "PARTIAL"


def render_markdown(records: List[ResultRecord]) -> str:
    """The verification section of ``forge report``."""
    from forge.verification.results import render_results_markdown

    lines = ["# Verification results", ""]
    if not records:
        lines.append("No verification results yet — run `forge test`.")
        return "\n".join(lines) + "\n"
    lines += [f"Overall: **{overall_status(records)}**", "",
              "| result | status | up to date | detail | file |", "|---|---|---|---|---|"]
    for r in records:
        detail = r.reason or ", ".join(
            f"{i['interface']} {i['matched']}/{i['expected']}" if i.get("expected")
            else f"{i['interface']} {i.get('observed', 0)} words"
            for i in r.payload.get("interfaces") or [])
        fresh = f"no — changed: {r.stale}" if r.stale else "yes"
        lines.append(f"| {r.name} | {r.status} | {fresh} | {detail} | {r.path.name} |")
    lines.append("")
    for r in records:
        if r.path.name.endswith(".results.json"):
            lines.append(render_results_markdown(r.payload))
    return "\n".join(lines) + "\n"
