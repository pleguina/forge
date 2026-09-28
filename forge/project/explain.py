"""Answer "why did FORGE do that?" for anything it decided.

FORGE makes a great many decisions on the user's behalf — it connects two
ports, infers a family, derives a width, predicts an HLS port, inserts a
crossing, or refuses to do any of those. Every one of those decisions was
already *recorded*: :class:`forge.ir.model.MatchingEvidence` carries
per-connection wiring method, coordinates, protocols, widths, cardinality
results and every rejected candidate; :mod:`forge.project.discovery` records
:class:`~forge.project.evidence.Evidence` for everything it infers. What was
missing was a way to ask.

This module is that way. It resolves a target — a diagnostic code, a module,
a port, a connection, a clock, an HLS kernel, a generated artifact — against
whichever sources can speak to it, and returns a deterministic
:class:`Explanation`.

Two rules govern what comes back:

* **The strongest available source wins.** Where the design resolves into
  the canonical IR, the explanation comes *from the IR*, because that is
  what the generators actually consume. Resolution is a pure function of the
  project configuration — it needs no prior ``forge build`` — so this is the
  normal case from the moment ``forge adopt`` has run. Discovery's own
  inference is the fallback for a design that does not resolve, and every
  explanation reports which of the two it came from, since they differ in
  authority.
* **An absent fact is reported absent.** "No contract declares this port" is
  an answer. Filling the gap with a plausible-looking guess would make this
  command worse than useless, since its entire purpose is to be trusted
  about what FORGE actually did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from forge.project.evidence import (
    DETERMINISTIC,
    HEURISTIC,
    INFERRED,
    Evidence,
    evidence_list,
    weakest_confidence,
)
from forge.project.status import ProjectStatus, evaluate

#: Target kinds, as the prefix a user types (``clock:clk``) or the shape the
#: target has (``module.port``).
KIND_DIAGNOSTIC = "diagnostic"
KIND_MODULE = "module"
KIND_PORT = "port"
KIND_CONNECTION = "connection"
KIND_CLOCK = "clock"
KIND_RESET = "reset"
KIND_HLS = "hls"
KIND_ARTIFACT = "artifact"

_DIAGNOSTIC_RE = re.compile(r"^(?:ATG|FWV)\d{3,}$", re.I)
_PREFIXED_RE = re.compile(r"^(connection|clock|reset|hls|artifact|module|port):(.+)$", re.I)


@dataclass
class ExplanationSection:
    """One headed block of an explanation."""

    heading: str
    lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"heading": self.heading, "lines": list(self.lines)}


@dataclass
class Explanation:
    """A deterministic account of one decision.

    Attributes:
        target: What was asked about, as typed.
        kind: One of the ``KIND_*`` constants.
        summary: One line naming the subject.
        sections: The body, in display order.
        evidence: The chain the conclusion rests on.
        source: Where the account came from — ``"canonical IR"`` when the
            design has been resolved, ``"source discovery"`` when it has
            not. Reported because the two differ in authority.
    """

    target: str
    kind: str
    summary: str
    sections: List[ExplanationSection] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    source: str = "source discovery"

    @property
    def confidence(self) -> str:
        """No stronger than the weakest step behind it."""
        return weakest_confidence(self.evidence)

    @property
    def distinct_evidence(self) -> List[Evidence]:
        """:attr:`evidence` with repeats removed, first-seen order kept.

        Several sections legitimately rest on the same fact — a port's width
        is both an HDL fact and half the reason a match was accepted — and
        listing it twice makes the account look padded rather than thorough.
        Compared by serialised form because ``Evidence.value`` may be a dict,
        which is not hashable.
        """
        seen = set()
        distinct: List[Evidence] = []
        for item in self.evidence:
            key = repr(item.to_dict())
            if key in seen:
                continue
            seen.add(key)
            distinct.append(item)
        return distinct

    def section(self, heading: str, lines: List[str]) -> None:
        """Append a section, skipping it entirely when it has nothing to say.

        A report full of empty headings reads as though FORGE knows things it
        does not.
        """
        if lines:
            self.sections.append(ExplanationSection(heading, lines))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "kind": self.kind,
            "summary": self.summary,
            "source": self.source,
            "confidence": self.confidence,
            "sections": [s.to_dict() for s in self.sections],
            "evidence": evidence_list(self.distinct_evidence),
        }


class UnknownTarget(ValueError):
    """The target names nothing in this project.

    A ``ValueError`` subclass so ``forge.core.cli.envelope.status_for_exception``
    classifies it as the user's input being wrong (exit 1), not FORGE
    falling over.
    """


# ── Entry point ───────────────────────────────────────────────────────────

def explain(target: str, root: "Path | str" = ".", *,
            status: Optional[ProjectStatus] = None) -> Explanation:
    """Explain *target* in the project at (or above) *root*.

    Args:
        target: A diagnostic code (``ATG037``), a module (``shaper``), a port
            (``shaper.shaped``), or a prefixed form (``connection:…``,
            ``clock:clk``, ``hls:regression``, ``artifact:path``).
        root: Anywhere inside the project.
        status: An assessment already computed, to avoid re-scanning.
    """
    kind, subject = classify_target(target)

    if kind == KIND_DIAGNOSTIC:
        # The catalogue is project-independent, so this one form works even
        # outside a project — which is exactly when someone pastes a code
        # from a CI log and wants to know what it means. Inside a project it
        # also reports where the code actually fired, which is usually the
        # half the reader came for.
        return _explain_diagnostic(target, subject, status or _try_evaluate(root))

    resolved = status or evaluate(root)
    if resolved.discovery is None:
        raise UnknownTarget(
            f"cannot explain {target!r}: there is no FORGE project at {root}. "
            f"Run `forge adopt {root}` first."
        )

    if kind == KIND_CLOCK or kind == KIND_RESET:
        return _explain_clock_or_reset(target, subject, resolved, kind)
    if kind == KIND_HLS:
        return _explain_hls(target, subject, resolved)
    if kind == KIND_ARTIFACT:
        return _explain_artifact(target, subject, resolved)
    if kind == KIND_CONNECTION:
        return _explain_connection(target, subject, resolved)
    if kind == KIND_PORT:
        return _explain_port(target, subject, resolved)
    return _explain_module(target, subject, resolved)


def _try_evaluate(root: "Path | str") -> Optional[ProjectStatus]:
    """Assess the project, or ``None`` when there is not one.

    Best-effort by design: explaining a diagnostic code must keep working
    outside any project, since a code pasted from a CI log is the most
    common way this command is reached.
    """
    try:
        status = evaluate(root)
    except Exception:  # noqa: BLE001
        return None
    return status if status.discovery is not None else None


def classify_target(target: str) -> Tuple[str, str]:
    """``"shaper.shaped"`` -> ``(KIND_PORT, "shaper.shaped")``.

    An explicit ``kind:subject`` prefix always wins, so a module that happens
    to be called ``clock`` stays reachable as ``module:clock``.
    """
    text = target.strip()
    if not text:
        raise UnknownTarget("nothing to explain — pass a module, port, clock or diagnostic code")

    prefixed = _PREFIXED_RE.match(text)
    if prefixed:
        prefix = prefixed.group(1).lower()
        subject = prefixed.group(2).strip()
        return ({"port": KIND_PORT, "module": KIND_MODULE}.get(prefix, prefix), subject)

    if _DIAGNOSTIC_RE.match(text):
        return KIND_DIAGNOSTIC, text.upper()
    if "." in text and not text.startswith("."):
        return KIND_PORT, text
    return KIND_MODULE, text


# ── Diagnostics ───────────────────────────────────────────────────────────

def _explain_diagnostic(
    target: str, code: str, status: Optional[ProjectStatus],
) -> Explanation:
    """What a code means, plus wherever it fired in this project.

    The description comes from the generated diagnostic catalogue rather
    than a second copy kept here — the catalogue is already the one place
    every ``ATG``/``FWV`` code is documented, and a divergent second copy is
    exactly the kind of drift that catalogue exists to prevent.
    """
    from forge.docsgen.diagnostics_registry import DIAGNOSTICS

    definition = DIAGNOSTICS.get(code)
    if definition is None:
        raise UnknownTarget(
            f"{code} is not a known diagnostic code. See `forge --help` or the "
            f"diagnostic catalogue in docs/reference/diagnostics.md."
        )

    explanation = Explanation(
        target=target,
        kind=KIND_DIAGNOSTIC,
        summary=f"{code} — {definition.description}",
        source="diagnostic catalogue",
        evidence=[Evidence("diagnostic catalogue", "registered_code", code, DETERMINISTIC)],
    )
    explanation.section("Severity", [definition.default_severity])
    explanation.section("How to fix", [definition.remediation] if definition.remediation else [])

    if status is not None:
        occurrences = [
            d for d in list(status.report.errors) + list(status.report.warnings)
            if d.code == code
        ]
        explanation.section(
            "In this project",
            [f"{d.message}" for d in occurrences]
            or ["not currently reported for this project"],
        )
    return explanation


# ── Modules ───────────────────────────────────────────────────────────────

def _explain_module(target: str, name: str, status: ProjectStatus) -> Explanation:
    discovery = status.discovery
    unit = discovery.unit(name)
    if unit is None:
        candidates = ", ".join(sorted(u.name for u in discovery.units)[:8]) or "(none)"
        raise UnknownTarget(
            f"no module named {name!r} in this project. Modules found: {candidates}"
        )

    explanation = Explanation(
        target=target,
        kind=KIND_MODULE,
        summary=f"{unit.name} — {unit.language} module, {len(unit.ports)} ports",
        evidence=[
            Evidence(str(unit.path), "declared_in_source", unit.name, DETERMINISTIC),
            Evidence(str(unit.path), "ports_read_from_source", len(unit.ports), DETERMINISTIC),
        ],
    )
    explanation.section("Source", [str(unit.path)])
    explanation.section("Ports", [
        f"{'input ' if p.direction == 'in' else 'output' if p.direction == 'out' else 'inout '} "
        f"{('[%d:0] ' % (p.width - 1)) if p.width > 1 else ''}{p.name}"
        for p in unit.ports.values()
    ])
    explanation.section("Clock / reset", [
        *(f"clock  {name}" for name in unit.clock_ports),
        *(f"reset  {name}" for name in unit.reset_ports),
    ])

    graph = discovery.instantiation_graph
    explanation.section("Instantiates", list(graph.get(unit.name, ())))
    explanation.section("Instantiated by", [
        other for other, deps in sorted(graph.items()) if unit.name in deps
    ])

    contract = status.paths.contract_root / f"{unit.name}.interface.yaml" if status.paths else None
    if contract is not None and contract.is_file():
        text = contract.read_text()
        state = "draft (unreviewed)" if "normalization_status: draft" in text else "ready"
        explanation.section("Contract", [str(contract), f"normalization_status: {state}"])
        explanation.evidence.append(
            Evidence(str(contract), "contract_present", state, DETERMINISTIC))
    else:
        explanation.section("Contract", ["none — this module has no interface contract"])

    explanation.section("Connections", [
        f"{c.producer}  ->  {c.consumer}"
        for c in discovery.connections
        if c.producer.startswith(f"{unit.name}.") or c.consumer.startswith(f"{unit.name}.")
    ])
    explanation.section("Outside FORGE's envelope", list(unit.unsupported))
    if unit.unsupported:
        explanation.evidence.append(
            Evidence(str(unit.path), "scan_limitation", unit.unsupported[0], DETERMINISTIC))
    return explanation


# ── Ports ─────────────────────────────────────────────────────────────────

def _explain_port(target: str, endpoint: str, status: ProjectStatus) -> Explanation:
    """The plan's worked example: why is this port wired where it is?"""
    discovery = status.discovery
    module_name, _, port_name = endpoint.partition(".")
    unit = discovery.unit(module_name)
    if unit is None:
        raise UnknownTarget(f"no module named {module_name!r} in this project")
    port = unit.ports.get(port_name)
    if port is None:
        available = ", ".join(sorted(unit.ports)[:10]) or "(none)"
        raise UnknownTarget(
            f"module {module_name!r} has no port {port_name!r}. Its ports: {available}"
        )

    direction = {"in": "input", "out": "output", "inout": "inout"}[port.direction]
    explanation = Explanation(
        target=target,
        kind=KIND_PORT,
        summary=f"{endpoint} — {direction}, {port.width} bit",
        evidence=[
            Evidence(str(unit.path), "port_direction", direction, DETERMINISTIC),
            Evidence(str(unit.path), "port_width", port.width, DETERMINISTIC),
        ],
    )
    slice_text = f"[{port.width - 1}:0] " if port.width > 1 else ""
    explanation.section("HDL", [f"{direction} {slice_text}{port.name}", f"in {unit.path}"])
    explanation.section("Contract", _contract_role_lines(status, module_name, port_name))

    # What FORGE decided to do with it, strongest source first.
    resolved = _ir_connections_for(status, endpoint)
    if resolved is not None:
        explanation.source = "canonical IR"
        connection, evidence_lines, ir_evidence = resolved
        if connection:
            explanation.section("Resolved connection", [connection])
            explanation.section("Why", evidence_lines)
        else:
            # No "Why" block: there is no decision to justify, only the fact
            # that the resolved design leaves this port alone.
            explanation.section("Resolved connection", evidence_lines)
        explanation.evidence.extend(ir_evidence)
    else:
        for connection in discovery.connections:
            if endpoint in (connection.producer, connection.consumer):
                other = (connection.consumer if endpoint == connection.producer
                         else connection.producer)
                explanation.section("Inferred connection", [
                    other,
                    "not yet resolved through the IR — this is what `forge adopt` "
                    "inferred from the sources",
                ])
                explanation.section("Why", [e.describe() for e in connection.evidence])
                explanation.evidence.extend(connection.evidence)
                break

    # A refusal is a decision too, and the one most worth explaining.
    for ambiguity in discovery.ambiguities:
        if ambiguity.subject == endpoint:
            explanation.section("Why FORGE stopped", [ambiguity.question])
            explanation.section("Candidates it considered", list(ambiguity.options))
            if ambiguity.action:
                explanation.section("How to decide", [ambiguity.action.description])
            explanation.evidence.extend(ambiguity.evidence)
            break
    else:
        if endpoint in discovery.external_inputs:
            explanation.section("Resolved connection", [
                "exposed at the top level as an external input — no producer in this design"])
        elif endpoint in discovery.external_outputs:
            explanation.section("Resolved connection", [
                "exposed at the top level as an external output — no consumer in this design"])

    explanation.section("Confidence", [explanation.confidence])
    return explanation


def _contract_role_lines(status: ProjectStatus, module: str, port: str) -> List[str]:
    """What the module's contract says about this port, if anything.

    A slim contract states the role name and nothing else, which is a real
    and reportable answer: the integration semantics FORGE cannot infer
    (``wiring_kind``, ``protocol``, coordinates) have not been declared.
    """
    import yaml

    if status.paths is None:
        return []
    contract = status.paths.contract_root / f"{module}.interface.yaml"
    if not contract.is_file():
        return ["no contract for this module"]
    try:
        document = yaml.safe_load(contract.read_text()) or {}
    except Exception:  # noqa: BLE001
        return [f"{contract.name} could not be parsed"]

    roles = ((document.get("ip_interface") or {}).get("roles") or {})
    for role_name, body in (roles.items() if isinstance(roles, dict) else []):
        fields = body if isinstance(body, dict) else {}
        if str(fields.get("raw_port") or role_name) != port:
            continue
        lines = [f"role: {role_name}"]
        lines.extend(
            f"{key}: {value}" for key, value in sorted(fields.items())
            if key != "notes"
        )
        if len(lines) == 1:
            lines.append(
                "no wiring_kind, protocol or coordinates declared — the semantics "
                "FORGE cannot infer are still unset"
            )
        return lines
    return [f"no role in {contract.name} binds to this port"]


# ── Connections ───────────────────────────────────────────────────────────

def _explain_connection(target: str, subject: str, status: ProjectStatus) -> Explanation:
    """Explain a resolved connection by IR id, or by ``producer->consumer``."""
    project = _build_ir(status)
    if project is None:
        # Resolution is a pure function of the project configuration, so
        # reaching here means the design does not resolve at all — not that
        # it merely has not been built.
        raise UnknownTarget(
            "this design does not resolve into the IR, so its connections cannot be "
            "explained. Run `forge check` to see what is wrong with it, or explain a "
            "port instead — that falls back to what discovery inferred."
        )

    wanted = subject.replace(" ", "")
    for connection in project.design.connections:
        producer = _endpoint_label(connection.producer)
        consumer = _endpoint_label(connection.consumer)
        if wanted in (connection.id, f"{producer}->{consumer}"):
            return _connection_explanation(target, connection, producer, consumer)

    sample = ", ".join(c.id for c in project.design.connections[:5]) or "(none)"
    raise UnknownTarget(
        f"no connection {subject!r} in the resolved design. Connection ids look "
        f"like: {sample}"
    )


def _connection_explanation(target, connection, producer, consumer) -> Explanation:
    evidence = [
        Evidence(
            "canonical IR", "wiring_method",
            connection.wiring_method or "unrecorded",
            DETERMINISTIC if connection.wiring_method
            and connection.wiring_method != "heuristic" else HEURISTIC,
        ),
    ]
    explanation = Explanation(
        target=target,
        kind=KIND_CONNECTION,
        summary=f"{producer}  ->  {consumer}",
        source="canonical IR",
        evidence=evidence,
    )
    explanation.section("Wiring method", [connection.wiring_method or "not recorded"])

    match = connection.matching_evidence
    if match is not None:
        explanation.section("Why", _matching_evidence_lines(match))
        explanation.section("Rejected candidates", [
            f"{_endpoint_label(rejected.producer)} — {rejected.reason}"
            for rejected in match.rejected_candidates
        ])
        if match.producer_width is not None and match.consumer_width is not None:
            evidence.append(Evidence(
                "canonical IR", "width",
                f"{match.producer_width} -> {match.consumer_width}", DETERMINISTIC))

    explanation.section("Transformations", [
        f"{t.kind}" + (f" ({t.detail})" if getattr(t, "detail", None) else "")
        for t in connection.transformations
    ])
    explanation.section("Domain crossings", [
        *(["crosses a clock domain"] if connection.crosses_clock_domain else []),
        *(["crosses a reset domain"] if connection.crosses_reset_domain else []),
    ])
    explanation.section("Confidence", [explanation.confidence])
    return explanation


def _matching_evidence_lines(match) -> List[str]:
    """Render only what the evidence actually carries.

    ``MatchingEvidence`` leaves every field unset where the underlying
    contract role does not exist — a handshake pin, a clock, a
    no-contract auto-match. Printing those as ``None`` would suggest FORGE
    considered something it did not.
    """
    pairs = [
        ("wiring_kind", match.producer_wiring_kind, match.consumer_wiring_kind),
        ("protocol", match.producer_protocol, match.consumer_protocol),
        ("width", match.producer_width, match.consumer_width),
        ("coordinates", match.producer_coordinates, match.consumer_coordinates),
    ]
    lines = [
        f"{label}: {produced} -> {consumed}"
        for label, produced, consumed in pairs
        if produced is not None or consumed is not None
    ]
    if match.gather_scatter_pattern:
        lines.append(f"pattern: {match.gather_scatter_pattern}")
    if match.cdc_declared:
        lines.append(f"cdc declared: {match.cdc_declared}")
    for label, result in (("producer", match.producer_cardinality),
                          ("consumer", match.consumer_cardinality)):
        if result is not None:
            verdict = "satisfied" if result.satisfied else "VIOLATED"
            lines.append(
                f"{label} cardinality: {result.actual_count} against "
                f"{result.bound} — {verdict}"
            )
    return lines


def _endpoint_label(endpoint) -> str:
    instance = getattr(endpoint, "instance", None) or getattr(endpoint, "instance_id", None)
    port = getattr(endpoint, "port", None) or getattr(endpoint, "pin", None)
    return f"{instance}.{port}" if instance and port else str(endpoint)


def _ir_connections_for(
    status: ProjectStatus, endpoint: str,
) -> Optional[Tuple[str, List[str], List[Evidence]]]:
    """The IR's account of what this port is wired to, if the IR exists."""
    project = _build_ir(status)
    if project is None:
        return None
    for connection in project.design.connections:
        producer = _endpoint_label(connection.producer)
        consumer = _endpoint_label(connection.consumer)
        if endpoint not in (producer, consumer):
            continue
        other = consumer if endpoint == producer else producer
        lines = [f"wiring method: {connection.wiring_method or 'not recorded'}"]
        if connection.matching_evidence is not None:
            lines.extend(_matching_evidence_lines(connection.matching_evidence))
        evidence = [Evidence(
            "canonical IR", "wiring_method",
            connection.wiring_method or "unrecorded",
            DETERMINISTIC if connection.wiring_method
            and connection.wiring_method != "heuristic" else HEURISTIC,
        )]
        return other, lines, evidence
    return "", ["no connection in the resolved design touches this port"], [
        Evidence("canonical IR", "unconnected", endpoint, DETERMINISTIC)]


_IR_CACHE: Dict[str, Any] = {}


def _build_ir(status: ProjectStatus):
    """Resolve the design into the canonical IR, or ``None`` if it cannot be.

    Rebuilt rather than read back from a previously emitted ``design.ir.json``
    so an explanation always describes the design as it is *now* — an
    explanation of a stale IR would be confidently wrong, which is the worst
    thing this command could be. Cached per design path because several
    sections of one explanation ask for it.
    """
    if status.paths is None:
        return None
    design = status.paths.design_yml
    if not design.is_file():
        return None
    key = str(design)
    if key in _IR_CACHE:
        return _IR_CACHE[key]
    try:
        from forge.ir import build_project_ir_with_match_report

        project, _cfg, _report = build_project_ir_with_match_report(
            design,
            contracts_from=str(status.paths.modules_yml)
            if status.paths.modules_yml.is_file() else None,
        )
    except Exception:  # noqa: BLE001
        # A design that will not resolve is explained from discovery
        # instead. `forge check` is where that failure gets reported —
        # `forge explain` must not become a second, worse validator.
        project = None
    _IR_CACHE[key] = project
    return project


# ── Clocks and resets ─────────────────────────────────────────────────────

def _explain_clock_or_reset(
    target: str, name: str, status: ProjectStatus, kind: str,
) -> Explanation:
    discovery = status.discovery
    pool = discovery.clocks if kind == KIND_CLOCK else discovery.resets
    candidate = next((c for c in pool if c.name == name), None)
    if candidate is None:
        available = ", ".join(c.name for c in pool) or "(none)"
        raise UnknownTarget(
            f"no {kind} named {name!r} in this project. Candidates: {available}"
        )

    declared = (status.config.clock.port if kind == KIND_CLOCK
                else status.config.reset.port) if status.config else None
    explanation = Explanation(
        target=target,
        kind=kind,
        summary=f"{candidate.name} — {kind} on {len(candidate.ports)} modules",
        evidence=list(candidate.evidence),
    )
    explanation.section("Appears on", list(candidate.ports))
    explanation.section("Why FORGE reads it as a " + kind, [
        e.describe() for e in candidate.evidence
    ])
    explanation.section("Declared in forge.yml", [
        f"{kind}.port: {declared}"
        + ("" if declared == candidate.name else "  (this is a different port)")
    ] if declared else [])

    if kind == KIND_RESET:
        level = "low" if candidate.active_low else "high"
        explanation.section("Active level", [
            f"active {level}, read from the port name",
            "FORGE's generators wire every module reset to one top-level "
            "active-high ap_rst net and do not invert it"
            if candidate.active_low else
            "matches the polarity the generated top level drives",
        ])

    others = [c.name for c in pool if c.name != candidate.name]
    explanation.section("Other candidates in this design", others)
    explanation.section("Confidence", [explanation.confidence])
    return explanation


# ── HLS ───────────────────────────────────────────────────────────────────

def _explain_hls(target: str, name: str, status: ProjectStatus) -> Explanation:
    """What FORGE believes an HLS kernel's RTL interface will be, and why.

    An HLS module's real ports are only knowable after synthesis, so this
    reports the prediction *as* a prediction — including the predictor's own
    warnings, which are the honest record of what it could not resolve.
    """
    discovery = status.discovery
    candidate = next(
        (c for c in discovery.hls_candidates if c.top == name or c.path.stem == name), None)
    if candidate is None:
        available = ", ".join(c.top for c in discovery.hls_candidates) or "(none)"
        raise UnknownTarget(f"no HLS kernel named {name!r}. Kernels found: {available}")

    explanation = Explanation(
        target=target,
        kind=KIND_HLS,
        summary=f"{candidate.top} — HLS kernel in {candidate.path.name}",
        evidence=list(candidate.evidence),
    )
    explanation.section("Source", [str(candidate.path)])
    explanation.section("Why FORGE reads it as an HLS kernel", [
        e.describe() for e in candidate.evidence
    ])

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
        explanation.section("Predicted RTL interface", [
            f"{'input ' if p.direction == 'input' else 'output'} "
            f"{p.name}  [{p.width} bit]"
            for p in prediction.ports
        ])
        explanation.section("What the predictor could not resolve",
                            list(signature.warnings) + list(prediction.warnings))
        explanation.evidence.append(Evidence(
            str(candidate.path), "ports_predicted_from_cpp",
            len(prediction.ports), INFERRED,
        ))
    except Exception as exc:  # noqa: BLE001
        explanation.section("Predicted RTL interface", [
            f"prediction failed: {exc}",
            "the kernel's real ports will be known once it is synthesised",
        ])

    _add_hls_maturity(explanation, candidate, status)
    explanation.section("Confidence", [explanation.confidence])
    return explanation


def _add_hls_maturity(explanation: Explanation, candidate, status: ProjectStatus) -> None:
    """Where this kernel sits on the maturity ladder, and the reconciliation
    if it has reached one.

    The half of the answer that matters once someone has actually built the
    IP: not "this is a prediction" but "here is precisely where the
    prediction turned out to be wrong, and why".
    """
    from forge.project.hls_maturity import (
        MATURITY_LEVELS,
        RECONCILED,
        assess_candidate,
        is_at_least,
    )

    assessment = assess_candidate(candidate, root=status.root)
    ladder = " -> ".join(
        f"[{level}]" if level == assessment.level else level
        for level in MATURITY_LEVELS
    )
    explanation.section("Maturity", [
        f"{assessment.level} — {assessment.description}",
        ladder,
    ])
    explanation.evidence.extend(assessment.evidence)

    if not is_at_least(assessment.level, RECONCILED) or assessment.reconciliation is None:
        explanation.section("Next", [
            "run `forge hls run --stages csim,synth` so the real RTL ports are known, "
            "then re-run this command to see them reconciled against the prediction",
        ])
        return

    reconciliation = assessment.reconciliation
    explanation.section("Reconciliation", [
        f"predicted {reconciliation.predicted_count}, "
        f"synthesised {reconciliation.synthesized_count}, "
        f"matched {len(reconciliation.matched)}",
    ])
    explanation.section("Where prediction and synthesis differ", [
        line
        for difference in reconciliation.differences
        for line in (
            [difference.describe()]
            + ([f"  reason: {difference.reason}"] if difference.reason else [])
        )
    ])


# ── Artifacts ─────────────────────────────────────────────────────────────

def _explain_artifact(target: str, subject: str, status: ProjectStatus) -> Explanation:
    """Where a generated file came from, and whether it is still current."""
    import datetime

    root = status.paths.root if status.paths else status.root
    path = Path(subject).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    if not path.exists():
        raise UnknownTarget(f"no such artifact: {path}")

    explanation = Explanation(
        target=target,
        kind=KIND_ARTIFACT,
        summary=f"{status.paths.relative(path) if status.paths else path}",
        evidence=[Evidence(str(path), "file_exists", True, DETERMINISTIC)],
    )
    stat = path.stat()
    written = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    explanation.section("File", [str(path), f"{stat.st_size} bytes", f"written {written}"])

    produced_by = _producing_command(path, status)
    explanation.section("Produced by", [produced_by] if produced_by else [])

    inputs = _artifact_inputs(status)
    newer = [p for p in inputs if p.stat().st_mtime > stat.st_mtime]
    explanation.section("Currency", (
        [f"stale — {len(newer)} of its inputs have changed since it was written"]
        + [f"  {status.paths.relative(p) if status.paths else p}" for p in newer[:6]]
        if newer else
        ["current — no input has changed since it was written"]
    ))
    explanation.evidence.append(Evidence(
        str(path), "newer_inputs", len(newer),
        DETERMINISTIC,
    ))

    provenance = (status.paths.generated_root / "provenance.json") if status.paths else None
    if provenance is not None and provenance.is_file():
        explanation.section("Provenance", [
            str(provenance),
            "run `forge inspect --explain-staleness` against it for the full record",
        ])
    explanation.section("Confidence", [explanation.confidence])
    return explanation


def _producing_command(path: Path, status: ProjectStatus) -> str:
    """Which FORGE command writes a file at this location.

    Keyed off the resolved project layout rather than the filename, so it
    stays right for a project whose top level is not called ``algo_top.v``.
    """
    if status.paths is None:
        return ""
    parents = set(path.parents)
    if status.paths.generated_root in parents or path.parent == status.paths.generated_root:
        return "forge build"
    if status.paths.contract_root in parents or path.parent == status.paths.contract_root:
        return "forge adopt (or `forge contract infer`)"
    if status.paths.project_config_root in parents or path.parent == status.paths.project_config_root:
        return "forge adopt"
    if status.paths.report_root in parents or path.parent == status.paths.report_root:
        return "forge report"
    return ""


def _artifact_inputs(status: ProjectStatus) -> List[Path]:
    """Everything a generated artifact is derived from."""
    from forge.project.paths import ROOT_CONFIG_NAME

    if status.config is None or status.paths is None:
        return []
    candidates = [
        status.config.root / ROOT_CONFIG_NAME,
        status.paths.design_yml,
        status.paths.modules_yml,
        *status.config.resolve_sources("rtl"),
        *status.config.resolve_sources("hls"),
    ]
    return [p for p in candidates if p.is_file()]
