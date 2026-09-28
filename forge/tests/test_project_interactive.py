"""
Tests for `forge adopt --interactive` — Phase D4.

The property under test throughout is the plan's constraint: *all answers
must become normal declarative config, no hidden interactive state*. So
almost every test here ends by re-planning adoption from the written
`forge.yml` and asserting the question is gone — not by inspecting anything
the prompt loop returned.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forge.project.adopt import plan_adoption
from forge.project.config import ForgeConfig
from forge.project.interactive import (
    EXCLUDE,
    Answer,
    apply_answer,
    options_for,
    render_question,
    resolve_interactively,
)


def _write_ambiguous_project(root: Path) -> Path:
    """One producer, two equally valid consumers — the plan's worked case."""
    rtl = root / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "producer.v").write_text(
        "module producer (input clk, input rst,\n"
        "                 output [15:0] payload_out);\nendmodule\n"
    )
    for name in ("consumer_a", "consumer_b"):
        (rtl / f"{name}.v").write_text(
            f"module {name} (input clk, input rst,\n"
            f"               input [15:0] payload_in);\nendmodule\n"
        )
    return root


def _answers(*replies: str):
    """A canned `ask` that plays *replies* in order."""
    it = iter(replies)
    return lambda _prompt: next(it)


def _silent(*_args, **_kwargs) -> None:
    pass


def test_an_answer_becomes_a_declaration_and_settles_the_question(tmp_path):
    root = _write_ambiguous_project(tmp_path)
    plan = plan_adoption(root)
    assert len(plan.unresolved) == 1

    config, answers, unanswered = resolve_interactively(
        plan.config, plan.unresolved, ask=_answers("1"), echo=_silent,
    )
    assert len(answers) == 1 and not unanswered
    config.save()

    # The answer is in the file the user owns, in the form they could have
    # typed themselves.
    written = yaml.safe_load((root / "forge.yml").read_text())
    assert written["connections"] == [
        {"from": "producer.payload_out", "to": "consumer_a.payload_in"}
    ]

    # And adoption, re-run from that file alone, no longer asks.
    again = plan_adoption(root)
    assert again.unresolved == []
    wired = [(c.producer, c.consumer, c.confidence) for c in again.discovery.connections]
    assert ("producer.payload_out", "consumer_a.payload_in", "deterministic") in wired


def test_the_declaration_reaches_the_generated_topology(tmp_path):
    """The point of the answer: it has to become real wiring, not just a
    silenced question."""
    root = _write_ambiguous_project(tmp_path)
    plan = plan_adoption(root)
    config, _answers_given, _ = resolve_interactively(
        plan.config, plan.unresolved, ask=_answers("2"), echo=_silent,
    )
    config.save()

    again = plan_adoption(root)
    design = yaml.safe_load(
        next(v for k, v in again.files.items() if k.name == "design.yml")
    )
    assert design["connections"] == [{
        "from": "producer", "to": "consumer_b",
        "port_map": [["payload_out", "payload_in"]],
    }]


def test_a_hand_written_answer_and_an_interactive_one_are_the_same_project(tmp_path):
    """There is no interactive state: typing the line yourself must produce
    byte-identical output to answering the question."""
    interactive_root = _write_ambiguous_project(tmp_path / "interactive")
    manual_root = _write_ambiguous_project(tmp_path / "manual")

    plan = plan_adoption(interactive_root, project_name="same_project")
    config, _, _ = resolve_interactively(
        plan.config, plan.unresolved, ask=_answers("1"), echo=_silent,
    )
    config.save()

    manual_plan = plan_adoption(manual_root, project_name="same_project")
    manual_plan.write(overwrite=True)
    manual_config = manual_root / "forge.yml"
    manual_config.write_text(
        manual_config.read_text()
        + "connections:\n"
          "- from: producer.payload_out\n"
          "  to: consumer_a.payload_in\n"
    )

    from_interactive = plan_adoption(interactive_root, project_name="same_project")
    from_manual = plan_adoption(manual_root, project_name="same_project")

    def rendered(plan_obj, name):
        return next(v for k, v in plan_obj.files.items() if k.name == name)

    assert rendered(from_interactive, "design.yml") == rendered(from_manual, "design.yml")
    assert from_interactive.unresolved == from_manual.unresolved == []


def test_skipping_leaves_the_question_open_and_writes_nothing(tmp_path):
    root = _write_ambiguous_project(tmp_path)
    plan = plan_adoption(root)

    config, answers, unanswered = resolve_interactively(
        plan.config, plan.unresolved, ask=_answers("s"), echo=_silent,
    )

    assert answers == []
    assert len(unanswered) == 1
    assert config.connections == []


def test_stopping_half_way_keeps_the_answers_already_given(tmp_path):
    """Ctrl-D after answering one question must not throw that answer away —
    each is independently valid."""
    root = tmp_path
    rtl = root / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "src_one.v").write_text(
        "module src_one (input clk, input rst, output [7:0] alpha_out);\nendmodule\n"
    )
    (rtl / "src_two.v").write_text(
        "module src_two (input clk, input rst, output [7:0] beta_out);\nendmodule\n"
    )
    for name, signal in (("dst_a", "alpha"), ("dst_b", "alpha")):
        (rtl / f"{name}.v").write_text(
            f"module {name} (input clk, input rst, input [7:0] {signal}_in);\nendmodule\n"
        )
    for name in ("dst_c", "dst_d"):
        (rtl / f"{name}.v").write_text(
            f"module {name} (input clk, input rst, input [7:0] beta_in);\nendmodule\n"
        )

    plan = plan_adoption(root)
    assert len(plan.unresolved) == 2

    def ask(_prompt):
        if not ask.asked:
            ask.asked = True
            return "1"
        raise EOFError
    ask.asked = False

    config, answers, unanswered = resolve_interactively(
        plan.config, plan.unresolved, ask=ask, echo=_silent,
    )

    assert len(answers) == 1
    assert len(config.connections) == 1
    assert len(unanswered) == 1


def test_an_invalid_reply_is_re_asked_rather_than_guessed(tmp_path):
    root = _write_ambiguous_project(tmp_path)
    plan = plan_adoption(root)
    prompts = []

    config, answers, _ = resolve_interactively(
        plan.config, plan.unresolved,
        ask=_answers("7", "consumer_x.payload_in", "2"),
        echo=prompts.append,
    )

    assert len(answers) == 1
    assert config.connections[0].consumer == "consumer_b.payload_in"
    assert sum("Enter 1-2" in p for p in prompts) == 2


def test_an_option_can_be_chosen_by_name(tmp_path):
    root = _write_ambiguous_project(tmp_path)
    plan = plan_adoption(root)

    config, answers, _ = resolve_interactively(
        plan.config, plan.unresolved,
        ask=_answers("consumer_b.payload_in"), echo=_silent,
    )

    assert len(answers) == 1
    assert config.connections[0].consumer == "consumer_b.payload_in"


def test_a_contested_consumer_records_the_pair_the_other_way_round(tmp_path):
    """Two producers whose only candidate is the same input: the subject is
    the *consumer* and the choice is the producer. The declaration written
    must still be (producer, consumer), not the pair as asked."""
    rtl = tmp_path / "rtl"
    rtl.mkdir(parents=True)
    for name in ("src_a", "src_b"):
        (rtl / f"{name}.v").write_text(
            f"module {name} (input clk, input rst, output [7:0] hit_out);\nendmodule\n"
        )
    (rtl / "sink.v").write_text(
        "module sink (input clk, input rst, input [7:0] hit_in);\nendmodule\n"
    )

    plan = plan_adoption(tmp_path)
    contested = [a for a in plan.unresolved if a.subject == "sink.hit_in"]
    assert contested, [a.subject for a in plan.unresolved]

    config, _, _ = resolve_interactively(
        plan.config, contested, ask=_answers("1"), echo=_silent,
    )

    declared = config.connections[0]
    assert declared.producer == "src_a.hit_out"
    assert declared.consumer == "sink.hit_in"


def test_a_clock_answer_settles_the_clock_question(tmp_path):
    rtl = tmp_path / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "fast.v").write_text(
        "module fast (input clk_fast, input rst, output [7:0] a_out);\nendmodule\n"
    )
    (rtl / "slow.v").write_text(
        "module slow (input clk_slow, input rst, input [7:0] a_in);\nendmodule\n"
    )

    plan = plan_adoption(tmp_path)
    clock_questions = [a for a in plan.unresolved if a.kind == "clock"]
    assert clock_questions

    config, answers, _ = resolve_interactively(
        plan.config, clock_questions, ask=_answers("1"), echo=_silent,
    )
    config.save()

    assert config.clock.port == answers[0].choice
    again = plan_adoption(tmp_path)
    assert not [a for a in again.unresolved if a.kind == "clock"]
    # The answer survives the re-adoption that regenerates the file.
    assert ForgeConfig.load(tmp_path / "forge.yml").clock.port == answers[0].choice


def test_a_top_answer_settles_the_top_question_and_picks_the_top(tmp_path):
    rtl = tmp_path / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "leaf.v").write_text(
        "module leaf (input clk, input rst);\nendmodule\n"
    )
    (rtl / "top_one.v").write_text(
        "module top_one (input clk, input rst);\n  leaf u_leaf ();\nendmodule\n"
    )
    (rtl / "top_two.v").write_text(
        "module top_two (input clk, input rst);\n  leaf u_leaf ();\nendmodule\n"
    )

    plan = plan_adoption(tmp_path)
    top_questions = [a for a in plan.unresolved if a.kind == "top"]
    assert top_questions

    config, answers, _ = resolve_interactively(
        plan.config, top_questions, ask=_answers("2"), echo=_silent,
    )
    config.save()
    chosen = answers[0].choice

    again = plan_adoption(tmp_path)
    assert not [a for a in again.unresolved if a.kind == "top"]
    assert again.discovery.structural_top == chosen
    assert ForgeConfig.load(tmp_path / "forge.yml").top == chosen


def test_a_module_question_offers_opaque_and_leaving_it_out(tmp_path):
    """An unsupported module's options are prose ways forward, so the two
    the project can actually record are named explicitly."""
    from forge.project.actions import Action
    from forge.project.discovery import UNSUPPORTED, Ambiguity, KIND_MODULE

    ambiguity = Ambiguity(
        id="unsupported:vendor_ip",
        classification=UNSUPPORTED,
        subject="vendor_ip",
        kind=KIND_MODULE,
        question="vendor_ip: 3 functional clock domains",
        options=("import as opaque…", "split it…", "exclude it…"),
        action=Action(id="resolve-unsupported-module", description="…"),
    )
    config = ForgeConfig(project="p", root=tmp_path)

    values = [value for value, _label in options_for(ambiguity)]
    assert values == ["opaque", EXCLUDE]

    opaque = apply_answer(config, Answer(ambiguity, "opaque"))
    assert opaque.modules["vendor_ip"].opaque

    # "Leave it out" is what already happens — it records nothing rather
    # than inventing a management value.
    excluded = apply_answer(config, Answer(ambiguity, EXCLUDE))
    assert excluded.modules == {}


def test_an_hls_top_answer_is_recorded_under_the_module(tmp_path):
    from forge.project.discovery import AMBIGUOUS, Ambiguity, KIND_HLS_TOP

    ambiguity = Ambiguity(
        id="hls-top:proj:kernel.cpp",
        classification=AMBIGUOUS,
        subject=str(tmp_path / "hls" / "kernel.cpp"),
        kind=KIND_HLS_TOP,
        question="kernel.cpp defines 3 functions…",
        options=("helper", "kernel_top", "other"),
    )
    config = ForgeConfig(project="p", root=tmp_path)

    answered = apply_answer(config, Answer(ambiguity, "kernel_top"))

    assert answered.modules["kernel"].top == "kernel_top"
    assert answered.to_dict()["modules"]["kernel"] == {
        "management": "managed", "top": "kernel_top",
    }


def test_a_declared_hls_top_settles_the_question(tmp_path):
    hls = tmp_path / "hls"
    hls.mkdir(parents=True)
    (hls / "kernel.cpp").write_text(
        "#pragma HLS inline\n"
        "void alpha(int a) { }\n"
        "void beta(int b) { }\n"
    )
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "block.v").write_text(
        "module block (input clk, input rst);\nendmodule\n"
    )

    plan = plan_adoption(tmp_path)
    hls_questions = [a for a in plan.unresolved if a.kind == "hls_top"]
    assert hls_questions

    config, answers, _ = resolve_interactively(
        plan.config, hls_questions, ask=_answers("2"), echo=_silent,
    )
    config.save()

    again = plan_adoption(tmp_path)
    assert not [a for a in again.unresolved if a.kind == "hls_top"]
    assert again.discovery.hls_candidates[0].top == answers[0].choice


def test_a_declared_connection_naming_a_missing_port_is_reported(tmp_path):
    """A typo in an answer must not read as a connection that exists."""
    root = _write_ambiguous_project(tmp_path)
    plan_adoption(root).write(overwrite=True)
    config_path = root / "forge.yml"
    config_path.write_text(
        config_path.read_text()
        + "connections:\n- from: producer.payload_out\n  to: consumer_a.nope_in\n"
    )

    again = plan_adoption(root)

    blocking = [a for a in again.unresolved if "nope_in" in a.question]
    assert len(blocking) == 1
    assert "not a port of any managed module" in blocking[0].question
    assert not any(
        c.consumer == "consumer_a.nope_in" for c in again.discovery.connections
    )


def test_a_broken_declaration_is_not_offered_as_a_question_to_answer(tmp_path):
    """It is fixed by editing, not by choosing between candidates — so the
    prompt loop must not present it as a multiple choice."""
    from forge.project.discovery import AMBIGUOUS, Ambiguity, KIND_CONNECTION
    from forge.project.interactive import answerable

    ambiguity = Ambiguity(
        id="connection:declared:a.o->b.nope",
        classification=AMBIGUOUS,
        subject="b.nope",
        kind=KIND_CONNECTION,
        question="forge.yml declares a.o -> b.nope, but b.nope is not a port…",
        options=("a.o", "b.i"),
    )

    assert answerable(ambiguity) is False

    config, answers, unanswered = resolve_interactively(
        ForgeConfig(project="p", root=tmp_path), [ambiguity],
        ask=_answers(), echo=_silent,
    )
    assert answers == [] and unanswered == [ambiguity]
    assert config.connections == []


def test_an_unrecordable_answer_does_not_lose_the_others(tmp_path):
    """A question kind with no forge.yml declaration behind it is reported
    and left open — the answers already given stay."""
    from forge.project.actions import Action
    from forge.project.discovery import AMBIGUOUS, Ambiguity

    root = _write_ambiguous_project(tmp_path)
    plan = plan_adoption(root)
    unrecordable = Ambiguity(
        id="future:something",
        classification=AMBIGUOUS,
        subject="whatever",
        kind="a_kind_from_the_future",
        question="a question this build cannot record an answer to",
        options=("one", "two"),
        action=Action(id="future", description="edit something"),
    )
    messages = []

    config, answers, unanswered = resolve_interactively(
        plan.config, [*plan.unresolved, unrecordable],
        ask=_answers("1", "1"), echo=messages.append,
    )

    assert len(answers) == 1
    assert len(config.connections) == 1
    assert unanswered == [unrecordable]
    assert any("Cannot record that answer" in m for m in messages)


def test_the_question_shows_the_equivalent_hand_edit(tmp_path):
    """Someone who would rather edit the file must be told what to write."""
    root = _write_ambiguous_project(tmp_path)
    plan = plan_adoption(root)

    text = render_question(plan.unresolved[0], index=1, total=1)

    assert "[1] consumer_a.payload_in" in text
    assert "[s] skip" in text
    assert "Equivalent edit:" in text
    assert "connections:" in text and "forge.yml" in text


def test_interactive_refuses_json_and_a_non_tty(tmp_path, capsys, monkeypatch):
    """Two ways of asking for a prompt where no one can answer it."""
    from forge.core.cli.main import build_parser

    _write_ambiguous_project(tmp_path)
    parser = build_parser()

    for extra, expected in (
        (["--json"], "cannot be combined with --json"),
        ([], "needs a terminal"),
    ):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        args = parser.parse_args(["adopt", str(tmp_path), "--interactive", *extra])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        assert exc.value.code == 1
        assert expected in capsys.readouterr().err


def test_interactive_cli_writes_the_answer_and_adopts_from_it(tmp_path, monkeypatch, capsys):
    """The command end to end, with a terminal simulated."""
    from forge.core.cli.main import build_parser

    root = _write_ambiguous_project(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _answers("1"))

    parser = build_parser()
    args = parser.parse_args(["adopt", str(root), "--interactive"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)

    out = capsys.readouterr().out
    assert exc.value.code == 0, out
    assert "Recorded 1 answer" in out

    written = yaml.safe_load((root / "forge.yml").read_text())
    assert written["connections"] == [
        {"from": "producer.payload_out", "to": "consumer_a.payload_in"}
    ]
    assert plan_adoption(root).unresolved == []
