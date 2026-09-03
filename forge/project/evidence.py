"""The one evidence model every inference in FORGE records itself with.

Before this module each subsystem that guessed something recorded *why* in
its own shape, or not at all: ``MatchReport.connection_evidence`` keeps a
bare method string, HLS port prediction returns free-text warnings, contract
inference kept nothing. That is why "why did FORGE connect these two ports?"
had no single answer to give back — the plan's guiding principle 3.4
("explain every automated decision") needs one structure that port
inference, clock inference, topology matching, HLS prediction, contract
reconciliation and diagnostics can all populate, and that ``forge explain``
can render without knowing which of them produced it.

An :class:`Evidence` is deliberately small — where the fact came from, which
rule turned it into a conclusion, the value concluded, and how much the
conclusion can be trusted. The confidence level is the field the rest of the
system reasons about: :data:`DETERMINISTIC` facts may be written into
contract truth, anything weaker may only be *proposed* to the user (the
plan's "never silently convert an uncertain guess into contract truth").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

#: The fact is proven by something authoritative — a port declared in the
#: module's own source, a value read from a tool report. Safe to write.
DETERMINISTIC = "deterministic"

#: The fact follows from a convention FORGE recognises and that held
#: unambiguously here (a single port named ``clk``, one uninstantiated
#: module). Safe to *propose*, and safe to write only where the plan's
#: ``forge fix`` safety rule allows it.
INFERRED = "inferred"

#: The fact comes from a name/shape heuristic that could reasonably be wrong.
#: Never written into contract truth without the user confirming it.
HEURISTIC = "heuristic"

#: FORGE could not conclude anything — recorded so the *absence* of a
#: conclusion is still explainable rather than silent.
UNKNOWN = "unknown"

CONFIDENCES = (DETERMINISTIC, INFERRED, HEURISTIC, UNKNOWN)

#: Strongest to weakest. Used by :func:`weakest_confidence`, so a conclusion
#: built from several pieces of evidence is never reported as stronger than
#: the weakest step it rests on.
_STRENGTH = {name: rank for rank, name in enumerate(CONFIDENCES)}


@dataclass(frozen=True)
class Evidence:
    """One recorded reason FORGE reached a conclusion.

    Attributes:
        source: Where the fact came from, as something a user can go and
            look at — a file path, ``"modules.yml"``, ``"vitis-hls-report"``.
        rule: The rule that turned the fact into a conclusion, as a stable
            identifier (``"port_name_match"``, ``"single_uninstantiated"``).
            Stable because ``forge explain`` and tests key on it.
        value: What was concluded. Any JSON-serialisable value.
        confidence: One of :data:`CONFIDENCES`.
    """

    source: str
    rule: str
    value: Any = None
    confidence: str = INFERRED

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCES:
            raise ValueError(
                f"invalid Evidence confidence {self.confidence!r}, "
                f"must be one of {CONFIDENCES}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "rule": self.rule,
            "value": self.value,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Evidence":
        return cls(
            source=str(data["source"]),
            rule=str(data["rule"]),
            value=data.get("value"),
            confidence=str(data.get("confidence", INFERRED)),
        )

    def describe(self) -> str:
        """One line for a human-readable explanation block."""
        value = "" if self.value is None else f" = {self.value}"
        return f"{self.rule}{value}  [{self.confidence}, from {self.source}]"


def weakest_confidence(evidence: Iterable[Evidence]) -> str:
    """The confidence of a conclusion resting on all of *evidence*.

    A chain is exactly as trustworthy as its weakest link, so this returns
    the weakest confidence present. An empty chain is :data:`UNKNOWN` — no
    evidence is not the same as good evidence.
    """
    ranks = [_STRENGTH[e.confidence] for e in evidence]
    if not ranks:
        return UNKNOWN
    return CONFIDENCES[max(ranks)]


def is_safe_to_write(evidence: Iterable[Evidence]) -> bool:
    """Whether a conclusion may be written into project configuration.

    Implements the plan's safety rule for ``forge adopt``/``forge fix``:
    only a conclusion the source *proves* is written without asking. Anything
    inferred or guessed is surfaced as an unresolved decision instead.
    """
    return weakest_confidence(evidence) == DETERMINISTIC


def evidence_list(evidence: Iterable[Evidence]) -> List[Dict[str, Any]]:
    """Serialise a chain for the JSON envelope / IR."""
    return [e.to_dict() for e in evidence]
