"""
Declarative cardinality for interface contract roles.

A role may declare an optional ``cardinality:`` block bounding how many
producers may drive it (input roles) or how many consumers it may drive
(output roles):

    roles:
      raw_hit:
        raw_port: raw_hit
        direction: input
        width: 32
        cardinality:
          producers: {min: 1, max: 1}   # exactly one producer, required

      decoded_hit:
        raw_port: decoded_hit
        direction: output
        width: 32
        cardinality:
          fanout: forbidden             # sugar for consumers: {max: 1}

``fanout: allowed|forbidden`` and ``completeness: required|optional`` are
shorthand for ``consumers.max``/the relevant bound's ``min`` — see
docs/IP_INTERFACE_POLICY.md "Declarative cardinality" for the full field
reference and worked examples.

This module has two halves:

* :func:`parse_cardinality` — parses and validates one role's
  ``cardinality:`` block in isolation (used by
  ``forge.topgen.ip.contract_verifier.ContractVerifier`` for structural
  validation, and by :func:`verify_cardinality` below to know what to
  check).
* :func:`verify_cardinality` — checks resolved cardinality against the
  connections ``forge.topgen.ip.matcher.auto_match_ports`` actually wired
  (plus candidates its first-driver-wins guard rejected — see
  ``MatchReport.rejected_fanin``). This is a design-level check: it needs
  the wired design, not just one contract in isolation.

Scope note (gather/scatter): cardinality is evaluated per individual
physical pin, after array/N-D roles are expanded to concrete port names.
There is no separate group-level cardinality DSL for scatter/gather today
— each expanded pin's cardinality is checked the same way a scalar role's
is, which is sufficient for the "gather/scatter expectations where
currently supported" requirement without inventing new syntax.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

MANY = "many"

KNOWN_CARDINALITY_KEYS = frozenset({"producers", "consumers", "fanout", "completeness"})
KNOWN_FANOUT_VALUES = frozenset({"allowed", "forbidden"})
KNOWN_COMPLETENESS_VALUES = frozenset({"required", "optional"})


class CardinalityError(ValueError):
    """A structurally invalid ``cardinality:`` block.

    Callers (``ContractVerifier``) catch this and turn it into a normal
    structured issue rather than letting it propagate — never raised past
    the contract-verification boundary.
    """


@dataclass(frozen=True)
class Bound:
    min: int
    max: Union[int, str]  # int, or the literal string "many" (unbounded)

    def satisfied(self, count: int) -> bool:
        if count < self.min:
            return False
        if self.max != MANY and count > self.max:
            return False
        return True

    def __str__(self) -> str:
        return f"[{self.min}, {'many' if self.max == MANY else self.max}]"


@dataclass(frozen=True)
class ResolvedCardinality:
    """At most one of these is set, matching the role's own direction —
    ``producers`` for an input role, ``consumers`` for an output role."""
    producers: Optional[Bound] = None
    consumers: Optional[Bound] = None


def _parse_bound(block: Dict[str, Any], key: str) -> Bound:
    raw = block[key]
    if not isinstance(raw, dict):
        raise CardinalityError(f"'{key}' must be a mapping with 'min'/'max', got: {raw!r}")
    mn = raw.get("min", 0)
    mx = raw.get("max", MANY)
    if not isinstance(mn, int) or isinstance(mn, bool) or mn < 0:
        raise CardinalityError(f"'{key}.min' must be a non-negative integer, got: {mn!r}")
    if mx != MANY and (not isinstance(mx, int) or isinstance(mx, bool) or mx < 0):
        raise CardinalityError(f"'{key}.max' must be a non-negative integer or 'many', got: {mx!r}")
    if mx != MANY and mx < mn:
        raise CardinalityError(f"'{key}.max' ({mx}) is less than '{key}.min' ({mn})")
    return Bound(min=mn, max=mx)


def parse_cardinality(role_spec: Dict[str, Any], *, direction: str) -> Optional[ResolvedCardinality]:
    """Parse and resolve a role's ``cardinality:`` block.

    Returns ``None`` when the role declares no ``cardinality:`` at all —
    absence is valid and imposes no constraint, same convention as
    ``protocol``/``member``. Raises :class:`CardinalityError`
    on a structurally invalid block (unknown keys, bad bounds, or a
    direction/key mismatch such as ``consumers`` on an input role).
    """
    block = role_spec.get("cardinality")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise CardinalityError(f"'cardinality' must be a mapping, got: {block!r}")

    unknown = set(block) - KNOWN_CARDINALITY_KEYS
    if unknown:
        raise CardinalityError(
            f"unknown cardinality key(s) {sorted(unknown)} — must be a "
            f"subset of {sorted(KNOWN_CARDINALITY_KEYS)}"
        )

    fanout = block.get("fanout")
    if fanout is not None and fanout not in KNOWN_FANOUT_VALUES:
        raise CardinalityError(f"'fanout' must be one of {sorted(KNOWN_FANOUT_VALUES)}, got: {fanout!r}")

    completeness = block.get("completeness")
    if completeness is not None and completeness not in KNOWN_COMPLETENESS_VALUES:
        raise CardinalityError(
            f"'completeness' must be one of {sorted(KNOWN_COMPLETENESS_VALUES)}, got: {completeness!r}"
        )

    producers = _parse_bound(block, "producers") if "producers" in block else None
    consumers = _parse_bound(block, "consumers") if "consumers" in block else None

    if direction == "input":
        if consumers is not None:
            raise CardinalityError(
                "'consumers' cardinality declared on an input role — did you "
                "mean 'producers'? 'consumers'/'fanout' only apply to output roles."
            )
        if fanout is not None:
            raise CardinalityError(
                "'fanout' declared on an input role — fanout bounds how many "
                "consumers an *output* role may drive; use 'producers.max' here instead."
            )
        min_, max_ = (producers.min, producers.max) if producers else (0, MANY)
        if completeness == "required":
            min_ = max(min_, 1)
        elif completeness == "optional":
            min_ = 0
        return ResolvedCardinality(producers=Bound(min=min_, max=max_))

    if direction == "output":
        if producers is not None:
            raise CardinalityError(
                "'producers' cardinality declared on an output role — did you "
                "mean 'consumers'? 'producers' only applies to input roles."
            )
        min_, max_ = (consumers.min, consumers.max) if consumers else (0, MANY)
        if fanout == "forbidden":
            max_ = 1
        elif fanout == "allowed":
            max_ = MANY
        if completeness == "required":
            min_ = max(min_, 1)
        elif completeness == "optional":
            min_ = 0
        return ResolvedCardinality(consumers=Bound(min=min_, max=max_))

    raise CardinalityError(
        f"'cardinality' declared on a role with direction {direction!r} — "
        "only 'input'/'output' roles are supported"
    )


def _role_pin_names(role_spec: Dict[str, Any]) -> List[str]:
    """Expand a role spec to its concrete physical pin names (same
    expansion `synthesize_ip_info_from_contract` uses for array/N-D
    roles)."""
    if "raw_port_prefix" in role_spec:
        prefix = role_spec["raw_port_prefix"]
        count = role_spec.get("count", 0)
        return [f"{prefix}{i}" for i in range(count)]
    if "raw_port_tpl" in role_spec:
        tpl = role_spec["raw_port_tpl"]
        dims = role_spec.get("dims") or []
        ranges = [range(d) for d in dims]
        return [tpl.format(*combo) for combo in itertools.product(*ranges)]
    if "raw_port" in role_spec:
        return [role_spec["raw_port"]]
    return []


@dataclass
class CardinalityIssue:
    severity: str  # 'error' | 'warning'
    role: str
    message: str

    def __str__(self) -> str:
        icon = "❌" if self.severity == "error" else "⚠️ "
        return f"  {icon} [{self.role}] {self.message}"


def verify_cardinality(design_cfg: Any, contracts: Dict[str, Any], match_report: Any) -> List[CardinalityIssue]:
    """Check every module instance's declared role cardinality against the
    connections ``auto_match_ports`` actually wired.

    Must be called with the same ``contracts``/``match_report`` that
    produced the design's connections — this is a post-matching check, not
    a per-contract structural one (that's :func:`parse_cardinality`, via
    ``ContractVerifier``).
    """
    issues: List[CardinalityIssue] = []

    def err(role: str, msg: str) -> None:
        issues.append(CardinalityIssue("error", role, msg))

    for mod in design_cfg.modules:
        key = mod.ip_info_key or mod.name
        contract = contracts.get(key) or contracts.get(mod.name)
        if contract is None:
            continue

        for role_name, role_spec in contract._roles.items():
            direction = role_spec.get("direction", "")
            try:
                resolved = parse_cardinality(role_spec, direction=direction)
            except CardinalityError:
                continue  # already reported by ContractVerifier — don't double-report
            if resolved is None:
                continue

            pins = _role_pin_names(role_spec)
            for i in range(max(1, mod.instances)):
                inst = mod.name if mod.instances == 1 else f"{mod.name}_{i}"
                for pin in pins:
                    if direction == "input" and resolved.producers is not None:
                        sink_key = (inst, pin)
                        n = len(match_report.rejected_fanin.get(sink_key, []))
                        n += sum(
                            1 for (_, _, dst_i, d_pin) in match_report.connection_evidence
                            if dst_i == inst and d_pin == pin
                        )
                        if not resolved.producers.satisfied(n):
                            err(
                                f"{mod.name}.{role_name}",
                                f"instance '{inst}' pin '{pin}': {n} producer(s) "
                                f"connected, expected {resolved.producers}",
                            )
                    elif direction == "output" and resolved.consumers is not None:
                        n = sum(
                            1 for (src_i, s_pin, _, _) in match_report.connection_evidence
                            if src_i == inst and s_pin == pin
                        )
                        if not resolved.consumers.satisfied(n):
                            err(
                                f"{mod.name}.{role_name}",
                                f"instance '{inst}' pin '{pin}': {n} consumer(s) "
                                f"connected, expected {resolved.consumers}",
                            )

    return issues
