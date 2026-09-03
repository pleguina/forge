"""
The explorer as an authoring tool — Phase J1/J2/J3, and `forge connect`.

The constraint the plan puts on this is the one worth testing: an authoring
action must write *normal project configuration*, and the UI must not
maintain a private parallel state. So these tests check that the page only
ever carries decisions computed elsewhere, that the declaration it shows is
the one FORGE actually reads back, and that the command it shows produces
the same file as answering the same question interactively.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from forge.analysis.design_explorer.authoring_join import (
    open_decisions_from_discovery,
    open_decisions_for_project,
)
from forge.analysis.design_explorer.graph_model import OpenDecision, build_design_graph
from forge.analysis.design_explorer.html_renderer import render_explorer_html
from forge.core.cli.main import build_parser
from forge.ir.build import build_project_ir
from forge.project.adopt import plan_adoption


def _ambiguous_project(root: Path) -> Path:
    rtl = root / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "producer.v").write_text(
        "module producer (input clk, input rst, output [15:0] payload_out);\nendmodule\n"
    )
    for name in ("consumer_a", "consumer_b"):
        (rtl / f"{name}.v").write_text(
            f"module {name} (input clk, input rst, input [15:0] payload_in);\nendmodule\n"
        )
    plan_adoption(root).write(overwrite=True)
    return root


def _run(args_list):
    parser = build_parser()
    args = parser.parse_args(args_list)
    try:
        args.func(args)
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)
    return 0


def _graph_payload(html_path: Path) -> dict:
    html = html_path.read_text()
    island = re.search(r'id="design-graph-data">(.*?)</script>', html, re.S)
    return json.loads(island.group(1))


# ── The data half: decisions come from discovery, never from the UI ───────

def test_open_decisions_are_discoverys_questions_verbatim(tmp_path):
    root = _ambiguous_project(tmp_path)
    plan = plan_adoption(root)

    decisions = open_decisions_from_discovery(
        plan.discovery, instance_ids=["producer", "consumer_a", "consumer_b"],
    )

    assert len(decisions) == 1
    decision = decisions[0]
    ambiguity = plan.unresolved[0]
    assert decision.subject == ambiguity.subject
    assert decision.question == ambiguity.question
    assert decision.candidates == ambiguity.options
    assert decision.node_id == "producer"


def test_a_decision_for_a_module_with_no_node_still_appears(tmp_path):
    """A question is not less real for having nothing to highlight — but the
    node id must be honestly empty rather than invented."""
    root = _ambiguous_project(tmp_path)
    plan = plan_adoption(root)

    decisions = open_decisions_from_discovery(plan.discovery, instance_ids=[])

    assert decisions and decisions[0].node_id == ""


def test_a_settled_project_has_no_open_decisions(tmp_path):
    root = _ambiguous_project(tmp_path)
    _run(["connect", "producer.payload_out", "consumer_b.payload_in",
          "--project", str(root)])

    assert open_decisions_for_project(root) == []


def test_a_directory_that_is_not_a_project_yields_nothing(tmp_path):
    assert open_decisions_for_project(tmp_path) == []


def test_a_broken_declaration_is_not_offered_as_a_choice(tmp_path):
    """A `connections:` entry naming a port nothing has is fixed by editing
    the entry, not by picking a candidate, so it must not appear as a
    decision — `forge check` reports it instead. The genuine question the
    broken entry failed to answer is still there."""
    root = _ambiguous_project(tmp_path)
    config = root / "forge.yml"
    config.write_text(
        config.read_text()
        + "connections:\n- from: producer.payload_out\n  to: consumer_a.nope\n"
    )

    decisions = open_decisions_for_project(root)

    assert [d.id for d in decisions] == ["connection:producer.payload_out"]
    assert not any(d.id.startswith("connection:declared:") for d in decisions)


# ── The declaration itself ────────────────────────────────────────────────

def test_the_declaration_matches_the_direction_of_the_question():
    producer_side = OpenDecision(
        id="q1", subject="a.out", question="?", candidates=("b.in",),
        subject_is_producer=True,
    )
    consumer_side = OpenDecision(
        id="q2", subject="b.in", question="?", candidates=("a.out",),
        subject_is_producer=False,
    )

    assert producer_side.declaration_for("b.in") == {"from": "a.out", "to": "b.in"}
    assert consumer_side.declaration_for("a.out") == {"from": "a.out", "to": "b.in"}


def test_the_page_carries_the_declaration_and_the_command(tmp_path):
    root = _ambiguous_project(tmp_path)
    project = build_project_ir(
        root / ".forge/project/design.yml",
        contracts_from=root / ".forge/project/modules.yml",
    )
    graph = build_design_graph(
        project, open_decisions=open_decisions_for_project(root),
    )
    out = tmp_path / "explorer.html"
    render_explorer_html(graph, out)

    payload = _graph_payload(out)
    assert payload["overlay_hashes"]["authoring"]
    choices = payload["open_decisions"][0]["choices"]
    assert {c["candidate"] for c in choices} == {
        "consumer_a.payload_in", "consumer_b.payload_in",
    }
    assert choices[0]["declaration"] == {
        "from": "producer.payload_out", "to": "consumer_a.payload_in",
    }
    # The page shows the command that writes it — and writes nothing itself.
    html = out.read_text()
    assert "forge connect " in html
    assert "Open decisions" in html


def test_a_graph_without_the_overlay_is_unchanged(tmp_path):
    """Every explorer that had no decisions overlay before must render
    exactly as it did."""
    root = _ambiguous_project(tmp_path)
    project = build_project_ir(
        root / ".forge/project/design.yml",
        contracts_from=root / ".forge/project/modules.yml",
    )

    graph = build_design_graph(project)

    assert graph.open_decisions == ()
    assert "authoring" not in graph.overlay_hashes


# ── forge connect: the action the page hands you ──────────────────────────

def test_connect_writes_the_declaration_and_settles_the_question(tmp_path, capsys):
    root = _ambiguous_project(tmp_path)

    code = _run(["connect", "producer.payload_out", "consumer_a.payload_in",
                 "--project", str(root)])

    assert code == 0, capsys.readouterr().out
    written = yaml.safe_load((root / "forge.yml").read_text())
    assert written["connections"] == [
        {"from": "producer.payload_out", "to": "consumer_a.payload_in"}
    ]
    assert plan_adoption(root).unresolved == []


def test_connect_and_interactive_produce_the_same_file(tmp_path):
    """Three routes to one declaration — the page's command, the prompt, and
    an editor — must not differ."""
    from forge.project.interactive import resolve_interactively

    via_command = _ambiguous_project(tmp_path / "command")
    via_prompt = _ambiguous_project(tmp_path / "prompt")

    _run(["connect", "producer.payload_out", "consumer_a.payload_in",
          "--project", str(via_command)])

    plan = plan_adoption(via_prompt)
    config, _answers, _ = resolve_interactively(
        plan.config, plan.unresolved, ask=lambda _p: "1", echo=lambda *_a: None,
    )
    config.save()

    left = yaml.safe_load((via_command / "forge.yml").read_text())
    right = yaml.safe_load((via_prompt / "forge.yml").read_text())
    assert left["connections"] == right["connections"]


def test_connect_refuses_an_endpoint_that_does_not_exist(tmp_path, capsys):
    root = _ambiguous_project(tmp_path)

    code = _run(["connect", "producer.nope", "consumer_a.payload_in",
                 "--project", str(root)])

    assert code == 1
    err = capsys.readouterr().err
    assert "not a port of any managed module" in err
    # And it says what the module does have.
    assert "producer.payload_out" in err
    assert "connections" not in (root / "forge.yml").read_text()


def test_connect_refuses_a_second_driver_for_one_consumer(tmp_path, capsys):
    rtl = tmp_path / "rtl"
    rtl.mkdir(parents=True)
    for name in ("src_a", "src_b"):
        (rtl / f"{name}.v").write_text(
            f"module {name} (input clk, input rst, output [7:0] hit_out);\nendmodule\n"
        )
    (rtl / "sink.v").write_text(
        "module sink (input clk, input rst, input [7:0] hit_in);\nendmodule\n"
    )
    plan_adoption(tmp_path).write(overwrite=True)

    assert _run(["connect", "src_a.hit_out", "sink.hit_in", "--project", str(tmp_path)]) == 0
    code = _run(["connect", "src_b.hit_out", "sink.hit_in", "--project", str(tmp_path)])

    assert code == 1
    assert "already declared as driven by" in capsys.readouterr().err

    # --force replaces rather than adding a second driver.
    assert _run(["connect", "src_b.hit_out", "sink.hit_in",
                 "--project", str(tmp_path), "--force"]) == 0
    written = yaml.safe_load((tmp_path / "forge.yml").read_text())
    assert written["connections"] == [{"from": "src_b.hit_out", "to": "sink.hit_in"}]


def test_connect_dry_run_writes_nothing(tmp_path, capsys):
    root = _ambiguous_project(tmp_path)
    before = (root / "forge.yml").read_text()

    code = _run(["connect", "producer.payload_out", "consumer_a.payload_in",
                 "--project", str(root), "--dry-run"])

    assert code == 0
    assert (root / "forge.yml").read_text() == before
    assert "[dry-run]" in capsys.readouterr().out


def test_connect_is_idempotent(tmp_path):
    root = _ambiguous_project(tmp_path)
    args = ["connect", "producer.payload_out", "consumer_a.payload_in",
            "--project", str(root)]

    assert _run(args) == 0
    assert _run(args) == 0

    written = yaml.safe_load((root / "forge.yml").read_text())
    assert len(written["connections"]) == 1


def test_connect_reports_json(tmp_path, capsys):
    root = _ambiguous_project(tmp_path)

    code = _run(["connect", "producer.payload_out", "consumer_a.payload_in",
                 "--project", str(root), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics"]["declaration"] == {
        "from": "producer.payload_out", "to": "consumer_a.payload_in",
    }
    assert payload["metrics"]["written"] is True


def test_connect_outside_a_project_says_so(tmp_path, capsys):
    code = _run(["connect", "a.out", "b.in", "--project", str(tmp_path)])

    assert code == 1
    assert "no forge.yml" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["nodot", "too.many.dots", ".leading", "trailing."])
def test_connect_rejects_a_malformed_endpoint(tmp_path, capsys, bad):
    code = _run(["connect", bad, "b.in", "--project", str(tmp_path)])

    assert code == 1
    assert "module.port" in capsys.readouterr().err
