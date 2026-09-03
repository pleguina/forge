"""The one shape a "what should I run next?" suggestion takes.

Every command in this CLI already hands the user next steps, but as bare
strings: ``CommandEnvelope.next_actions`` is a ``list[str]``, and
``ATGDiagnostic.action`` is a sentence. That is fine to print and useless to
act on — nothing downstream can tell whether a suggestion is a command it
could run, whether running it is safe, or whether ``forge fix`` could apply
it without asking. The plan calls this out directly ("this prevents
free-form 'try this' strings from spreading throughout the code").

:class:`Action` is that shape. It stays renderable as the plain string the
envelope already carries (:meth:`Action.render`), so adopting it costs
nothing at the call sites that only print.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Action:
    """One suggested next step, with enough structure to act on.

    Attributes:
        id: Stable identifier for the *kind* of step
            (``"configure-verification"``, ``"resolve-ambiguous-consumer"``).
            Tests and ``forge fix`` key on this, never on the prose.
        description: One sentence, in the imperative, saying what the step
            achieves — the part a human reads.
        command: The exact command that performs it, if one exists. ``None``
            for a step that needs a human decision (choosing a semantic
            family, say) rather than a command.
        auto_fixable: Whether ``forge fix`` can perform this step itself.
        safe: Whether performing it can only ever restate something the
            sources already prove. An unsafe action is never applied without
            explicit confirmation, per the plan's ``forge fix`` safety rule:
            a width corrected from real RTL is safe, an invented semantic
            family is not.
    """

    id: str
    description: str
    command: Optional[str] = None
    auto_fixable: bool = False
    safe: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "command": self.command,
            "auto_fixable": self.auto_fixable,
            "safe": self.safe,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Action":
        return cls(
            id=str(data["id"]),
            description=str(data["description"]),
            command=data.get("command"),
            auto_fixable=bool(data.get("auto_fixable", False)),
            safe=bool(data.get("safe", True)),
        )

    def render(self) -> str:
        """The one-line form for ``CommandEnvelope.next_actions``.

        The envelope's ``next_actions`` is a list of strings and stays that
        way — this is how a structured action degrades into it without the
        command's JSON consumers having to change.
        """
        if self.command:
            return f"{self.description} — run: {self.command}"
        return self.description


def dedupe(actions: Iterable[Action]) -> List[Action]:
    """Drop repeats by ``id``, keeping first-seen order.

    The same blocker reached from two checks must not print its remedy
    twice — matching ``from_diagnostic_report``'s de-duplication of the
    string form.
    """
    seen = set()
    out: List[Action] = []
    for action in actions:
        if action.id in seen:
            continue
        seen.add(action.id)
        out.append(action)
    return out


def render_all(actions: Iterable[Action]) -> List[str]:
    """De-duplicated :meth:`Action.render` strings, for the envelope."""
    return [a.render() for a in dedupe(actions)]
