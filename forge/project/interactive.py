"""Ask the user the questions adoption refused to answer — Phase D4.

``forge adopt`` writes what the sources prove and asks about everything
else. That is the right default, and it leaves a user holding a list of
questions and a file to edit. This module offers the other route: ask them
one at a time, right after the scan that raised them.

The plan's constraint is the whole design:

    All answers must become normal declarative config.
    No hidden interactive state.

So nothing here resolves anything itself. Every answer is written into
``forge.yml`` — the file the user owns — as the same declaration they could
have typed by hand, and adoption is then re-run from that file. An
interactively adopted project and a hand-edited one are the same project,
byte for byte; the transcript is a convenience, never a source of truth.
Delete an answer from ``forge.yml`` and the question comes back.

Kept out of the CLI layer (``forge/core/cli/``) so the mapping from a
question to the declaration that settles it is testable without a terminal:
:func:`apply_answer` takes an :class:`~forge.project.discovery.Ambiguity`
and a chosen option and returns the edited config.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Sequence, Tuple

from forge.project.config import (
    MANAGEMENT_MANAGED,
    MANAGEMENT_OPAQUE,
    DeclaredConnection,
    ForgeConfig,
    ModulePolicy,
)
from forge.project.discovery import (
    Ambiguity,
    KIND_CLOCK,
    KIND_CONNECTION,
    KIND_HLS_TOP,
    KIND_MODULE,
    KIND_TOP,
)

#: What the user typed to skip a question.
SKIP = "s"

#: The module-question answer that records nothing, because leaving a module
#: FORGE cannot model out of the design is already what happens.
EXCLUDE = "exclude"


@dataclass(frozen=True)
class Answer:
    """One question, and the option the user chose for it."""

    ambiguity: Ambiguity
    choice: str

    @property
    def summary(self) -> str:
        return f"{self.ambiguity.subject} -> {self.choice}"


class UnanswerableQuestion(ValueError):
    """A question this module has no declaration to express the answer as.

    Raised rather than silently skipped: a question FORGE asks and cannot
    record the answer to is a gap in ``forge.yml``, and hiding it would
    leave the user answering into a void.
    """


def apply_answer(config: ForgeConfig, answer: Answer) -> ForgeConfig:
    """Return *config* with *answer* recorded as ordinary declarative config.

    Pure: the returned config is a new object and nothing is written. The
    caller saves it, which is what makes "the answer is the file" true —
    there is no other place for the decision to hide.
    """
    ambiguity, choice = answer.ambiguity, answer.choice
    kind = ambiguity.kind

    if kind == KIND_CONNECTION:
        # The question comes in two directions — "which consumer for this
        # producer?" and "which producer for this consumer?" — and the
        # declaration is the same pair either way. `subject` says which end
        # was asked about; the chosen option is the other end.
        producer, consumer = (
            (ambiguity.subject, choice)
            if _is_producer_question(ambiguity)
            else (choice, ambiguity.subject)
        )
        declared = list(config.connections)
        if all(
            (d.producer, d.consumer) != (producer, consumer) for d in declared
        ):
            declared.append(DeclaredConnection(producer=producer, consumer=consumer))
        return replace(config, connections=declared)

    if kind == KIND_CLOCK:
        # Only the port changes: a frequency the user may have set is a
        # separate decision this question never asked about.
        return replace(config, clock=replace(config.clock, port=choice))

    if kind == KIND_TOP:
        return replace(config, top=choice)

    if kind == KIND_HLS_TOP:
        # `subject` is the C++ file; the module it becomes is named after it,
        # which is the same key `modules:` uses everywhere else.
        module = _module_name_for_source(ambiguity.subject)
        policies = dict(config.modules)
        existing = policies.get(module)
        policies[module] = ModulePolicy(
            management=existing.management if existing else MANAGEMENT_MANAGED,
            top=choice,
        )
        return replace(config, modules=policies)

    if kind == KIND_MODULE:
        if choice == EXCLUDE:
            # Already what happens to a module FORGE cannot model: it is left
            # out. Recording "leave it out" as config would invent a
            # `management:` value that means nothing to anything else.
            return config
        policies = dict(config.modules)
        policies[ambiguity.subject] = ModulePolicy(management=MANAGEMENT_OPAQUE)
        return replace(config, modules=policies)

    raise UnanswerableQuestion(
        f"no forge.yml declaration expresses an answer to a {kind!r} question"
    )


def answerable(ambiguity: Ambiguity) -> bool:
    """Can an answer to *ambiguity* be written into ``forge.yml``?

    A question with no options to choose between is not answerable by
    picking one — an undeliverable ``connections:`` entry, say, whose fix is
    an edit rather than a choice.
    """
    if ambiguity.kind == KIND_MODULE:
        return True
    if ambiguity.id.startswith("connection:declared:"):
        return False
    return bool(ambiguity.options)


def options_for(ambiguity: Ambiguity) -> List[Tuple[str, str]]:
    """The choices to offer, as ``(value, label)``.

    ``value`` is what gets written; ``label`` is what the user reads. They
    differ only for a module question, whose ``options`` are prose ways
    forward rather than values — of which exactly two are things a project
    can record: integrate it structurally without modelling it, or leave it
    out. Mining a value out of that prose would be guessing at our own
    wording, so the two are named here.
    """
    if ambiguity.kind == KIND_MODULE:
        return [
            (MANAGEMENT_OPAQUE,
             f"integrate {ambiguity.subject} as an opaque module — structure only, "
             f"FORGE does not model its internals"),
            (EXCLUDE,
             f"leave {ambiguity.subject} out of the FORGE-managed design "
             f"(what happens today)"),
        ]
    return [(option, option) for option in ambiguity.options]


def _is_producer_question(ambiguity: Ambiguity) -> bool:
    """Is the subject the producer (choices are consumers), or the reverse?

    Read from the action the ambiguity already carries, not re-derived from
    its wording — ``forge.project.discovery`` names these, and one of them
    changing must not silently flip the pair this writes.
    """
    action_id = ambiguity.action.id if ambiguity.action else ""
    return action_id == "resolve-ambiguous-consumer"


def _module_name_for_source(source: str) -> str:
    from pathlib import Path

    return Path(source).stem


# ── The prompt loop ───────────────────────────────────────────────────────

def render_question(ambiguity: Ambiguity, *, index: int, total: int) -> str:
    """The question as the user sees it, options numbered from 1."""
    lines = [
        f"[{index}/{total}] {ambiguity.question}",
        "",
    ]
    for number, (_value, label) in enumerate(options_for(ambiguity), start=1):
        lines.append(f"  [{number}] {label}")
    lines.append(f"  [{SKIP}] skip — decide later")
    if ambiguity.action:
        lines.append("")
        lines.append(f"  Equivalent edit: {ambiguity.action.description}")
    return "\n".join(lines)


def resolve_interactively(
    config: ForgeConfig,
    ambiguities: Sequence[Ambiguity],
    *,
    ask: Callable[[str], str],
    echo: Callable[[str], None] = print,
) -> Tuple[ForgeConfig, List[Answer], List[Ambiguity]]:
    """Put each answerable question to the user and record the answers.

    Args:
        config: The project config to record answers into.
        ambiguities: The questions, in the order they should be asked.
        ask: Prompt for one line of input. Injected so the loop is testable
            and so a caller can drive it from something other than stdin.
        echo: Where the questions are printed.

    Returns:
        ``(config, answers, unanswered)`` — the config with every answer
        applied, what was answered, and the questions skipped or not
        answerable. Nothing is written; the caller saves the config, and a
        run with no answers must leave the file alone.

    A ``KeyboardInterrupt`` or end of input stops the loop and keeps the
    answers given so far: quitting half way through must not throw away the
    decisions already made, and each is independently valid. A question this
    module cannot express as a declaration is reported and left open for the
    same reason — one unrecordable answer must not lose the others.
    """
    answers: List[Answer] = []
    unanswered: List[Ambiguity] = []

    askable = [a for a in ambiguities if answerable(a)]
    unanswered.extend(a for a in ambiguities if not answerable(a))

    for index, ambiguity in enumerate(askable, start=1):
        echo(render_question(ambiguity, index=index, total=len(askable)))
        options = options_for(ambiguity)
        try:
            choice = _read_choice(ask, options, echo=echo)
        except (EOFError, KeyboardInterrupt):
            echo("")
            echo("Stopped. Answers given so far are kept; the rest stay open.")
            unanswered.extend(askable[index - 1:])
            break
        if choice is None:
            unanswered.append(ambiguity)
            continue
        answer = Answer(ambiguity=ambiguity, choice=choice)
        try:
            config = apply_answer(config, answer)
        except UnanswerableQuestion as exc:
            # A question kind `answerable()` let through and `apply_answer`
            # has no declaration for. Reported and left open rather than
            # crashing the run: the user's other answers are still valid,
            # and this one has to be settled some other way.
            echo(f"  Cannot record that answer: {exc}")
            echo("")
            unanswered.append(ambiguity)
            continue
        answers.append(answer)
        echo("")

    return config, answers, unanswered


def _read_choice(
    ask: Callable[[str], str],
    options: Sequence[Tuple[str, str]],
    *,
    echo: Callable[[str], None],
) -> Optional[str]:
    """One valid option's value, ``None`` for skip. Re-asks on anything else.

    Nothing is guessed from a partial or misspelled answer: a wrong option
    here writes a wrong connection into the project.
    """
    values = [value for value, _label in options]
    while True:
        raw = ask("> ").strip().lower()
        if raw in (SKIP, ""):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(values):
            return values[int(raw) - 1]
        if raw in [v.lower() for v in values]:
            return next(v for v in values if v.lower() == raw)
        echo(f"  Enter 1-{len(values)}, or {SKIP} to skip.")
