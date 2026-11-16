"""A project's own top, simulated as written (``verification.target: existing``).

The nested_payload fixture keeps its structure in its own top: two named
instances of one module with different parameters, wired through renamed
nets, with a width set by a define in its compile list. None of that has to
be re-entered: forge.yml names the top, the compile list and the data, and
`forge test` compares the original top against the expected output.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

from forge.core.cli.main import build_parser
from forge.project.status import PASS, evaluate
from forge.verification.auto import read_filelist

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "foreign" / "nested_payload"
needs_verilator = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")

FORGE_YML = """project: nested
top: payload_top
clock: {port: clk}
reset: {port: rst_n, active: low}
verification:
  target: existing
  filelist: sim/files.f
  input: in.json
  expected: exp.json
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "nested_payload"
    shutil.copytree(FIXTURE, root)
    xs = [random.Random(5).randrange(1 << 16) for _ in range(30)]
    (root / "in.json").write_text(json.dumps({"sample": xs}))
    (root / "exp.json").write_text(json.dumps({"result": [(x << 3) & 0xFFFF for x in xs]}))
    (root / "forge.yml").write_text(FORGE_YML)
    monkeypatch.chdir(root)
    return root


def _run(capsys, *argv):
    args = build_parser().parse_args(list(argv))
    try:
        args.func(args)
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, json.loads(out.out) if "--json" in argv else out.out, out.err


def test_the_compile_list_is_read(project):
    sources, incs, defines = read_filelist(project / "sim" / "files.f")

    assert [s.name for s in sources] == ["scale.v", "payload_top.v"]
    assert incs == [(project / "rtl").resolve()]
    assert defines == {"PAYLOAD_W": "16"}


def test_the_plan_is_the_original_top_with_its_define(project, capsys):
    code, plan, err = _run(capsys, "test", "--plan", "--json")

    assert code == 0, err
    (level,) = plan["levels"]
    assert level["level"] == "existing:payload_top" and level["defines"] == {"PAYLOAD_W": "16"}
    assert [(i["name"], i["width"]) for i in level["interfaces"]] == [("sample", 16), ("result", 16)]
    assert not (project / ".forge" / "generated").exists()


def test_check_judges_only_what_this_mode_uses(project):
    sections = {s.name: s for s in evaluate(project).sections}

    assert set(sections) == {"Existing top", "Verification"}
    assert sections["Existing top"].state == PASS


def test_a_missing_top_is_named(project, capsys):
    (project / "forge.yml").write_text(FORGE_YML.replace("top: payload_top\n", ""))

    code, _, err = _run(capsys, "test", "--plan")

    assert code != 0 and "set top:" in err


@needs_verilator
def test_the_original_top_is_verified_and_reported(project, capsys):
    code, doc, err = _run(capsys, "test", "--backend", "verilator", "--json")
    assert code == 0, err
    assert doc["metrics"]["levels"] == {"existing:payload_top[verilator]": "VERIFIED"}

    code, report, _ = _run(capsys, "report", "--json")
    assert report["metrics"]["verification"] == "VERIFIED"

    (project / "rtl" / "payload_defs.vh").write_text("`define PAYLOAD_W 8\n")
    _, report, _ = _run(capsys, "report", "--json")
    assert report["metrics"]["verification"] == "OUT_OF_DATE"


@needs_verilator
def test_it_works_after_adoption_left_questions_open(project, capsys):
    """Adoption cannot reproduce this top (one module feeding itself), so its
    questions stay open; simulating the existing top does not need them."""
    from forge.project.adopt import plan_adoption

    (project / "forge.yml").unlink()
    plan_adoption(project).write()
    config = (project / "forge.yml").read_text()
    (project / "forge.yml").write_text(config + FORGE_YML.split("reset: {port: rst_n, active: low}\n")[1])

    code, doc, err = _run(capsys, "test", "--backend", "verilator", "--json")

    assert code == 0, err
    assert doc["metrics"]["verification"] == "VERIFIED"
