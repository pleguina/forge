"""How far an HLS module's interface can be trusted, and why.

An RTL module's ports are a fact: they are declared in its source, FORGE
reads them, and there is nothing to be uncertain about. An HLS module's are
not. Until Vitis HLS has synthesised it, its RTL interface is a *prediction*
made from the C++ signature and its pragmas — a good prediction, made by
rules taken from real synthesis, and still a prediction. After synthesis it
is a fact, and the two may differ.

FORGE reported that distinction as a single warning and nothing else, which
left the interesting question unanswered: *how far* along is this module,
and where exactly did the prediction turn out to be wrong? The plan's Phase
I asks for both — a maturity ladder (I1) and a prediction-versus-synthesis
reconciliation (I2).

This module is those two. It is deliberately free of any dependency on
Vitis HLS being installed: maturity is read from what is on disk, and
reconciliation compares two port lists. The tool is needed to *produce* the
synthesised side, not to reason about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from forge.project.evidence import DETERMINISTIC, HEURISTIC, INFERRED, Evidence

# ── The maturity ladder ───────────────────────────────────────────────────

#: The C++ was parsed — argument names, types and pragmas are known — but
#: nothing has been said about RTL ports yet.
INFERRED_FROM_CPP = "inferred_from_cpp"
#: RTL ports have been predicted from the C++ by
#: :mod:`forge.hls.port_prediction`. Good, rule-based, and unconfirmed.
PREDICTED = "predicted"
#: Vitis HLS has run and a ``component.xml`` exists, so the real ports are
#: knowable — but nobody has compared them to the prediction yet.
SYNTHESIZED = "synthesized"
#: Predicted and synthesised have been compared and every difference is
#: classified. This is the first level at which the contract can be trusted.
RECONCILED = "reconciled"
#: The reconciled interface has passed verification.
VERIFIED = "verified"

#: Weakest to strongest. The order *is* the ladder — index into it to
#: compare two levels.
MATURITY_LEVELS = (
    INFERRED_FROM_CPP, PREDICTED, SYNTHESIZED, RECONCILED, VERIFIED,
)

_DESCRIPTIONS = {
    INFERRED_FROM_CPP: "C++ signature parsed; RTL ports not predicted yet",
    PREDICTED: "RTL ports predicted from C++; not confirmed against synthesis",
    SYNTHESIZED: "synthesised, but the prediction has not been reconciled with it",
    RECONCILED: "predicted and synthesised interfaces agree, or every difference is classified",
    VERIFIED: "reconciled interface has passed verification",
}


def describe(level: str) -> str:
    return _DESCRIPTIONS.get(level, "unknown maturity")


def is_at_least(level: str, minimum: str) -> bool:
    """Whether *level* is at or above *minimum* on the ladder."""
    try:
        return MATURITY_LEVELS.index(level) >= MATURITY_LEVELS.index(minimum)
    except ValueError:
        return False


# ── Reconciliation ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PortDifference:
    """One way the prediction and the synthesised interface disagree."""

    kind: str  # "width" | "unexpected" | "missing" | "direction"
    port: str
    predicted: Any = None
    synthesized: Any = None
    #: Why this difference happens, where FORGE recognises the cause. An
    #: unexplained difference is reported unexplained rather than given a
    #: plausible-sounding reason.
    reason: str = ""

    def describe(self) -> str:
        if self.kind == "width":
            return (
                f"{self.port}: predicted {self.predicted} bits, "
                f"synthesised {self.synthesized} bits"
            )
        if self.kind == "direction":
            return (
                f"{self.port}: predicted {self.predicted}, "
                f"synthesised {self.synthesized}"
            )
        if self.kind == "unexpected":
            return f"{self.port}: present in the built IP, not predicted"
        return f"{self.port}: predicted, absent from the built IP"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "port": self.port,
            "predicted": self.predicted,
            "synthesized": self.synthesized,
            "reason": self.reason,
            "description": self.describe(),
        }


@dataclass
class Reconciliation:
    """The result of comparing a predicted interface with a synthesised one."""

    module: str
    predicted_count: int = 0
    synthesized_count: int = 0
    matched: List[str] = field(default_factory=list)
    differences: List[PortDifference] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def agrees(self) -> bool:
        return not self.differences

    @property
    def maturity(self) -> str:
        """:data:`RECONCILED` once the comparison has been made.

        Reconciled does not mean *identical*: a difference FORGE can explain
        is still reconciled. What it means is that nothing about this
        interface is unaccounted for any more.
        """
        return RECONCILED

    def differences_of(self, kind: str) -> List[PortDifference]:
        return [d for d in self.differences if d.kind == kind]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "predicted": self.predicted_count,
            "synthesized": self.synthesized_count,
            "matched": len(self.matched),
            "agrees": self.agrees,
            "differences": [d.to_dict() for d in self.differences],
            "counts": {
                kind: len(self.differences_of(kind))
                for kind in ("width", "direction", "unexpected", "missing")
            },
        }

    def report(self) -> str:
        """The human-readable block, in the shape the plan specifies."""
        lines = [
            "HLS interface reconciliation",
            "",
            "Module",
            f"  {self.module}",
            "",
            "Predicted ports",
            f"  {self.predicted_count}",
            "",
            "Matched",
            f"  {len(self.matched)}",
        ]
        for kind, label in (
            ("width", "Width differences"),
            ("direction", "Direction differences"),
            ("unexpected", "Unexpected ports"),
            ("missing", "Missing ports"),
        ):
            found = self.differences_of(kind)
            if not found:
                continue
            lines.extend(["", label, f"  {len(found)}"])
            for difference in found:
                lines.append(f"    {difference.describe()}")
                if difference.reason:
                    lines.append(f"      reason: {difference.reason}")
        if self.agrees:
            lines.extend(["", "The prediction matches the built IP exactly."])
        return "\n".join(lines)


#: Differences FORGE can explain. Keyed by a predicate over the difference,
#: so a cause is only ever attached where it genuinely applies — an
#: unexplained width difference is far more useful than a confidently wrong
#: explanation of one.
def _explain(kind: str, port: str, predicted: Any, synthesized: Any) -> str:
    if kind == "width" and isinstance(predicted, int) and isinstance(synthesized, int):
        if predicted <= 0:
            # A zero-width prediction is not a *wrong* width, it is the
            # predictor reporting that it could not resolve the type at all
            # (an `ap_uint<CONSTANT>` whose constant lives in a header it
            # could not follow). Calling that byte-rounding would be a
            # confidently wrong explanation of a difference whose real cause
            # is already known and reported.
            return (
                "the predictor could not resolve this argument's width from the "
                "C++ source, so it had no prediction to be wrong about — the "
                "built IP is authoritative here"
            )
        if synthesized > predicted and synthesized % 8 == 0:
            return (
                "the synthesised port is byte-rounded; HLS pads a struct or "
                "sub-byte member up to a byte boundary"
            )
        if synthesized < predicted:
            return (
                "the synthesised port is narrower than predicted — the argument's "
                "resolved element type is smaller than the source suggested"
            )
    if kind == "unexpected" and port.endswith(("_ap_vld", "_ap_ack", "_o_ap_vld")):
        return "a handshake pin the block protocol adds around a data port"
    if kind == "unexpected" and port.startswith("ap_"):
        return "a block-level control pin from the module's ap_ctrl protocol"
    return ""


def reconcile(
    module: str,
    predicted: Sequence[Any],
    synthesized: Sequence[Dict[str, Any]],
) -> Reconciliation:
    """Compare a predicted RTL interface against the built IP's real one.

    Args:
        module: Module name, for the report.
        predicted: ``PredictedPort``-shaped objects (``name``, ``direction``,
            ``width``) — what :mod:`forge.hls.port_prediction` produced.
        synthesized: ``{"name", "direction", "width"}`` dicts — what
            ``forge.contracts.parser.parse_component`` read out of the
            built IP's ``component.xml``.

    Matching is by port name, because that is what the two sides actually
    share: a positional match would silently pair unrelated ports the moment
    HLS emits one more pin than predicted, which is the common case.
    """
    predicted_by_name = {str(getattr(p, "name", "")): p for p in predicted}
    synthesized_by_name = {
        str(p.get("name", "")): p for p in synthesized if p.get("name")
    }

    result = Reconciliation(
        module=module,
        predicted_count=len(predicted_by_name),
        synthesized_count=len(synthesized_by_name),
    )

    for name in sorted(set(predicted_by_name) & set(synthesized_by_name)):
        want, got = predicted_by_name[name], synthesized_by_name[name]
        differed = False

        want_width, got_width = _width_of(want), _width_of(got)
        if want_width is not None and got_width is not None and want_width != got_width:
            result.differences.append(PortDifference(
                kind="width", port=name,
                predicted=want_width, synthesized=got_width,
                reason=_explain("width", name, want_width, got_width),
            ))
            differed = True

        want_dir, got_dir = _direction_of(want), _direction_of(got)
        if want_dir and got_dir and want_dir != got_dir:
            result.differences.append(PortDifference(
                kind="direction", port=name,
                predicted=want_dir, synthesized=got_dir,
                reason=_explain("direction", name, want_dir, got_dir),
            ))
            differed = True

        if not differed:
            result.matched.append(name)

    for name in sorted(set(synthesized_by_name) - set(predicted_by_name)):
        result.differences.append(PortDifference(
            kind="unexpected", port=name,
            synthesized=_width_of(synthesized_by_name[name]),
            reason=_explain("unexpected", name, None, None),
        ))
    for name in sorted(set(predicted_by_name) - set(synthesized_by_name)):
        result.differences.append(PortDifference(
            kind="missing", port=name,
            predicted=_width_of(predicted_by_name[name]),
            reason=_explain("missing", name, None, None),
        ))

    result.evidence = [
        Evidence("built IP component.xml", "synthesized_port_count",
                 result.synthesized_count, DETERMINISTIC),
        Evidence("hls port prediction", "predicted_port_count",
                 result.predicted_count, INFERRED),
        Evidence("reconciliation", "differences", len(result.differences),
                 DETERMINISTIC),
    ]
    return result


def _width_of(port: Any) -> Optional[int]:
    value = port.get("width") if isinstance(port, dict) else getattr(port, "width", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _direction_of(port: Any) -> str:
    raw = port.get("direction") if isinstance(port, dict) else getattr(port, "direction", "")
    text = str(raw or "").strip().lower()
    # The two sides spell direction differently — the predictor says
    # "input"/"output", component.xml says "in"/"out" — so both normalise
    # before comparison rather than every caller remembering to.
    return {"in": "input", "out": "output", "input": "input", "output": "output"}.get(
        text, text
    )


def find_component_xml(root: Path, module: str) -> Optional[Path]:
    """The built IP's ``component.xml`` for *module*, if one exists.

    Searched under the directories a Vitis HLS run actually writes into, and
    matched on the module name appearing in the path — an HLS project emits
    one ``component.xml`` per solution, so an unfiltered ``rglob`` in a
    repository with several modules returns the wrong one about as often as
    the right one.

    Returns ``None`` rather than raising: not having built yet is the normal
    state of an adopted project, not an error.
    """
    search_roots = [
        root / "build", root / "build_hls", root / ".forge" / "cache",
        root / "ip_packages", root,
    ]
    for base in search_roots:
        if not base.is_dir():
            continue
        try:
            candidates = sorted(base.rglob("component.xml"))
        except OSError:
            continue
        for candidate in candidates:
            if module in candidate.parts or module in str(candidate):
                return candidate
    return None


# ── Reading maturity off a project ────────────────────────────────────────

@dataclass
class ModuleMaturity:
    """One HLS module's position on the ladder, and how it was determined."""

    module: str
    level: str
    evidence: List[Evidence] = field(default_factory=list)
    reconciliation: Optional[Reconciliation] = None

    @property
    def description(self) -> str:
        return describe(self.level)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "maturity": self.level,
            "description": self.description,
            "evidence": [e.to_dict() for e in self.evidence],
            "reconciliation": (
                self.reconciliation.to_dict() if self.reconciliation else None
            ),
        }


def assess_candidate(candidate: Any, *, root: Path) -> ModuleMaturity:
    """Assess an :class:`~forge.project.discovery.HlsCandidate` in place.

    The convenience wrapper the health check and ``forge explain`` both use,
    so neither has to know how to run the port predictor or where a built IP
    lives. A prediction that fails leaves the module at
    :data:`INFERRED_FROM_CPP` — the honest level, since nothing has been
    predicted — rather than raising into a health report.
    """
    predicted: List[Any] = []
    try:
        from forge.hls.cpp_signature import parse_signature
        from forge.hls.port_prediction import predict_ports

        signature = parse_signature(candidate.path, candidate.top)
        prediction = predict_ports(
            signature.args,
            block_protocol=signature.block_protocol,
            returns_value=signature.returns_value,
            return_width=signature.return_width,
        )
        predicted = list(prediction.ports)
    except Exception:  # noqa: BLE001
        predicted = []

    return assess(
        candidate.top,
        source=candidate.path,
        predicted=predicted,
        component_xml=find_component_xml(Path(root), candidate.top),
    )


def assess(
    module: str,
    *,
    source: Optional[Path] = None,
    predicted: Optional[Sequence[Any]] = None,
    component_xml: Optional[Path] = None,
    verified: bool = False,
) -> ModuleMaturity:
    """Where *module* sits on the ladder, from what is on disk.

    Needs no Vitis HLS: a ``component.xml`` either exists or it does not,
    and comparing two port lists is arithmetic. The tool is required to
    *produce* the synthesised side, never to reason about it.
    """
    evidence: List[Evidence] = []
    level = INFERRED_FROM_CPP

    if source is not None:
        evidence.append(Evidence(str(source), "cpp_source_present", True, DETERMINISTIC))
    if predicted:
        level = PREDICTED
        evidence.append(Evidence(
            str(source) if source else "hls port prediction",
            "ports_predicted_from_cpp", len(predicted), INFERRED,
        ))

    if component_xml is not None and component_xml.is_file():
        level = SYNTHESIZED
        evidence.append(Evidence(
            str(component_xml), "built_ip_present", True, DETERMINISTIC))

        if predicted:
            from forge.contracts.parser import parse_component

            try:
                component = parse_component(component_xml)
            except Exception:  # noqa: BLE001 — an unreadable component.xml
                # leaves the module honestly at SYNTHESIZED rather than
                # claiming a reconciliation that did not happen.
                component = None
            if component is not None:
                reconciliation = reconcile(
                    module, predicted, component.get("ports") or [])
                evidence.extend(reconciliation.evidence)
                level = VERIFIED if verified else RECONCILED
                return ModuleMaturity(module, level, evidence, reconciliation)

    if verified and is_at_least(level, RECONCILED):
        level = VERIFIED
    return ModuleMaturity(module, level, evidence)
