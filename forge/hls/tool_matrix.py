"""Which Vitis HLS versions the port predictor has actually been checked
against — Phase I3.

:mod:`forge.hls.port_prediction` predicts the RTL interface Vitis HLS will
generate for a C++ kernel. Its rules were derived from real synthesis
output, and HLS port naming moves between tool versions: a prediction
validated against one release is evidence about that release and nothing
else. Stating otherwise is the failure mode this module exists to prevent —
"do not imply stability for versions not tested", in the plan's words.

So the support matrix here is not a hand-maintained list of versions we
believe work. It is **derived from the evidence on disk**: one golden file
per version under ``forge/tests/fixtures/hls_port_matrix/golden/``, each
holding the exact port list every fixture kernel synthesised to under that
version. A version with a golden file is validated by replaying the
prediction against it; a version without one is ``untested`` and says so.

Adding a version is therefore a data change: synthesise the fixture corpus
under the new release (the fixture README has the script), drop the port
lists in as ``golden/<version>.json``, and the matrix picks it up.

The comparison itself is not reimplemented here. It is
``forge.project.hls_maturity.reconcile`` — the same predicted-vs-synthesised
reconciliation Phase I2 built for a real built IP, which already classifies
each difference and explains the causes it recognises. A validation run and
a user's own ``forge explain hls:<module>`` therefore answer the same
question the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from forge.hls.port_prediction import Argument, predict_ports
from forge.project.hls_maturity import PortDifference, Reconciliation, reconcile

#: The fixture corpus: C++ sources, the predictor inputs for each, and one
#: golden port list per Vitis HLS version.
FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hls_port_matrix"
)
GOLDEN_ROOT = FIXTURE_ROOT / "golden"
CASES_FILE = FIXTURE_ROOT / "cases.json"

#: The corpus was replayed against this version's real output and every
#: difference is accounted for — either there is none, or the corpus
#: documents it and says why. Deliberately **not** a claim that the
#: predictor reproduces every port: it is a claim that nothing this version
#: does is unexplained. The documented gaps are counted in every report so
#: the two can never be confused.
VERIFIED = "verified"
#: This version's output differs from the prediction in a way nothing
#: accounts for. Predicted contracts should be treated as drafts here until
#: the difference is understood.
DIVERGENT = "divergent"
#: No golden output for this version. Not a judgement — an absence of
#: evidence, reported as one.
UNTESTED = "untested"


@dataclass
class CaseResult:
    """One fixture kernel, predicted and compared under one tool version."""

    case: str
    reconciliation: Reconciliation
    #: Differences the corpus documents for this kernel, each paired with
    #: the reason it records.
    documented_differences: List[Tuple[PortDifference, str]] = field(default_factory=list)
    #: Differences nothing accounts for — the ones that matter.
    unexplained_differences: List[PortDifference] = field(default_factory=list)

    @property
    def status(self) -> str:
        return DIVERGENT if self.unexplained_differences else VERIFIED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case": self.case,
            "status": self.status,
            "predicted": self.reconciliation.predicted_count,
            "synthesized": self.reconciliation.synthesized_count,
            "matched": len(self.reconciliation.matched),
            "documented_differences": [
                {**difference.to_dict(), "reason": reason}
                for difference, reason in self.documented_differences
            ],
            "unexplained_differences": [
                d.to_dict() for d in self.unexplained_differences
            ],
        }


@dataclass
class VersionReport:
    """What replaying the whole fixture corpus under one version showed."""

    version: str
    cases: List[CaseResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.cases:
            return UNTESTED
        if any(case.status == DIVERGENT for case in self.cases):
            return DIVERGENT
        return VERIFIED

    @property
    def documented_gaps(self) -> List[Tuple[str, str]]:
        """``(case, reason)`` for every gap the corpus records, de-duplicated
        — one line per distinct reason rather than one per port."""
        seen: List[Tuple[str, str]] = []
        for case in self.cases:
            for _difference, reason in case.documented_differences:
                if (case.case, reason) not in seen:
                    seen.append((case.case, reason))
        return seen

    @property
    def unexplained(self) -> List[PortDifference]:
        return [d for case in self.cases for d in case.unexplained_differences]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "cases": [case.to_dict() for case in self.cases],
        }

    def report(self) -> str:
        lines = [
            f"Vitis HLS {self.version}: {self.status.upper()}",
        ]
        if self.status == UNTESTED:
            lines.append(
                "  no golden synthesis output for this version — FORGE's port "
                "predictions have not been validated against it"
            )
            return "\n".join(lines)
        for case in self.cases:
            lines.append(
                f"  {case.case}: {case.status} "
                f"({len(case.reconciliation.matched)}/"
                f"{case.reconciliation.synthesized_count} ports matched, "
                f"{len(case.documented_differences)} documented gap port(s))"
            )
            for difference in case.unexplained_differences:
                lines.append(f"    ! {difference.describe()}")
        gaps = self.documented_gaps
        if gaps:
            lines.append("")
            lines.append("  Known gaps (documented, not reproduced by the predictor):")
            for case_name, reason in gaps:
                lines.append(f"    {case_name}: {reason}")
        return "\n".join(lines)


# ── The corpus ────────────────────────────────────────────────────────────

def known_versions() -> List[str]:
    """Every Vitis HLS version there is real synthesis output for, oldest
    first (as sortable version strings, which these are)."""
    if not GOLDEN_ROOT.is_dir():
        return []
    return sorted(p.stem for p in GOLDEN_ROOT.glob("*.json"))


def reference_version() -> str:
    """The version the predictor's rules were derived from — the newest one
    with golden output, since a later release is what a user is most likely
    to be running.
    """
    versions = known_versions()
    if not versions:
        raise FileNotFoundError(f"no golden HLS output under {GOLDEN_ROOT}")
    return versions[-1]


def golden_ports(version: str) -> Dict[str, List[Dict[str, Any]]]:
    """The recorded port lists for *version*, by case name."""
    path = GOLDEN_ROOT / f"{version}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no golden HLS output for Vitis HLS {version}")
    return json.loads(path.read_text())


def cases() -> Dict[str, Dict[str, Any]]:
    """The predictor inputs for each fixture kernel.

    Held as data next to the C++ rather than as code inside a test, because
    validating a *new* tool version has to replay exactly the same inputs
    the old one was replayed with. Two copies of these argument lists would
    make a version comparison meaningless the first time they drifted.
    """
    return json.loads(CASES_FILE.read_text())


def _arguments(case: Dict[str, Any]) -> List[Argument]:
    return [
        Argument(
            name=spec["name"],
            width=spec.get("width", 0),
            dims=tuple(spec["dims"]) if spec.get("dims") else (),
            is_output=bool(spec.get("is_output", False)),
            mode=spec.get("mode", "ap_none"),
            partition_dim=spec.get("partition_dim"),
            is_struct=bool(spec.get("is_struct", False)),
        )
        for spec in case.get("arguments", [])
    ]


def _synthesized(ports: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Golden entries in the shape :func:`reconcile` takes.

    A recorded width is the Verilog slice text — ``"47:0"`` for a vector,
    ``"1"`` for a bare bit, or a parameterised expression like
    ``"C_M_AXI_GMEM_ADDR_WIDTH - 1:0"`` whose value is not known until
    synthesis. That last one is recorded as ``None`` rather than guessed, and
    ``reconcile`` skips a width comparison it has no number for.
    """
    out: List[Dict[str, Any]] = []
    for port in ports:
        text = str(port["width"]).strip()
        if ":" not in text:
            width: Optional[int] = 1
        else:
            high = text.split(":", 1)[0].strip()
            width = int(high) + 1 if high.isdigit() else None
        out.append({
            "name": port["name"], "direction": port["direction"], "width": width,
        })
    return out


def documented_reason(case: Dict[str, Any], difference: PortDifference) -> Optional[str]:
    """The corpus's reason for *difference*, or ``None`` if it has none.

    Gaps are declared per kernel in ``cases.json``, by exact port name
    wherever the port list is fixed — so "we know about this one" names a
    specific known port and cannot quietly swallow a real regression next
    to it. Prefixes are used only for the AXI bundles, whose port lists are
    width- and version-dependent, which is the documented reason they are
    not predicted in the first place.
    """
    for gap in case.get("documented_gaps", ()):
        reason = gap.get("reason", "")
        if difference.port in gap.get("ports", ()):
            return reason
        if any(difference.port.startswith(p) for p in gap.get("prefixes", ())):
            return reason
    return None


def predicted_ports(case: Dict[str, Any]) -> List[Any]:
    """The interface the predictor produces for one corpus kernel."""
    return predict_ports(
        _arguments(case),
        block_protocol=case.get("block_protocol", "ap_ctrl_hs"),
        returns_value=bool(case.get("returns_value", False)),
        return_width=case.get("return_width", 0),
    ).ports


def validate_case(name: str, case: Dict[str, Any], synthesized: Sequence[Dict[str, Any]]) -> CaseResult:
    """Predict *case*'s interface and compare it against real output."""
    result = reconcile(name, predicted_ports(case), _synthesized(synthesized))
    documented: List[Tuple[PortDifference, str]] = []
    unexplained: List[PortDifference] = []
    for difference in result.differences:
        reason = documented_reason(case, difference)
        if reason:
            documented.append((difference, reason))
        else:
            unexplained.append(difference)
    return CaseResult(
        case=name,
        reconciliation=result,
        documented_differences=documented,
        unexplained_differences=unexplained,
    )


def validate_version(version: str) -> VersionReport:
    """Replay every fixture kernel's prediction against *version*'s output.

    A version with no recorded output comes back ``untested`` with no cases
    — never as a pass, and never as a failure either.
    """
    report = VersionReport(version=version)
    try:
        golden = golden_ports(version)
    except FileNotFoundError:
        return report
    all_cases = cases()
    for name in sorted(golden):
        case = all_cases.get(name)
        if case is None:
            # Golden output for a kernel nothing knows how to predict. Not
            # silently skipped: it means the corpus and the case list have
            # drifted apart, which would quietly shrink what a "verified"
            # version was verified on.
            raise KeyError(
                f"golden output for {name!r} under Vitis HLS {version} has no "
                f"entry in {CASES_FILE.name}"
            )
        report.cases.append(validate_case(name, case, golden[name]))
    return report


def support_matrix() -> Dict[str, str]:
    """``{version: status}`` for every version with recorded output."""
    return {version: validate_version(version).status for version in known_versions()}


def status_for(version: Optional[str]) -> str:
    """The status of an arbitrary version string, ``untested`` when unknown.

    Takes the version as reported by the installed tool, so an unrecognised
    or unparseable one is ``untested`` rather than an error: a user running
    a release FORGE has never seen should be told exactly that.
    """
    if not version:
        return UNTESTED
    return support_matrix().get(version, UNTESTED)


def describe_status(status: str) -> str:
    """One sentence per status, for a report or a doctor line."""
    return {
        VERIFIED: (
            "the fixture corpus was replayed against real synthesis output "
            "from this version and every difference is accounted for — the "
            "predictor's known gaps are documented, and nothing else differs"
        ),
        DIVERGENT: (
            "this version's output differs from the prediction in ways FORGE "
            "cannot account for — treat predicted contracts as drafts and "
            "confirm them against the built IP"
        ),
        UNTESTED: (
            "FORGE has no synthesis output from this version, so its port "
            "predictions are unvalidated here — they may still be right, but "
            "nothing has checked"
        ),
    }.get(status, status)


# ── Version detection ─────────────────────────────────────────────────────

def installed_version(executable: str = "vitis_hls") -> Optional[str]:
    """The Vitis HLS release on ``PATH``, or ``None``.

    Parsed out of ``vitis_hls -version``'s banner, which carries the release
    as ``... v2024.1 ...``. Returns ``None`` for a missing tool, a launch
    failure, or a banner this cannot read — every one of which means the same
    thing to a caller: the version is unknown, so nothing about it may be
    claimed.
    """
    import re
    import shutil
    import subprocess

    resolved = shutil.which(executable)
    if not resolved:
        return None
    try:
        completed = subprocess.run(
            [resolved, "-version"], capture_output=True, text=True, timeout=60,
        )
    except Exception:  # noqa: BLE001
        return None
    match = re.search(r"\bv?(\d{4}\.\d+)\b", f"{completed.stdout}\n{completed.stderr}")
    return match.group(1) if match else None
