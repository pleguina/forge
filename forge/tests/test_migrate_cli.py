"""Coverage for `forge migrate` — bringing a whole project up to the schemas
this build understands (plan §11, F4).

`forge topgen migrate` already performed the individual migrations well, and
asked the user to know which files needed which one. That is the wrong
question to put to someone who has just upgraded FORGE. This command answers
theirs, and the properties worth defending are the ones that make a
migration command trustworthy rather than merely capable:

* **It performs nothing itself.** Every migration is delegated to the
  function in `forge.generation.migrate` that owns it, so this route and the
  file-by-file route cannot diverge.
* **Dry-run by default**, and a preview that is byte-identical to what
  `--apply` writes.
* **Idempotent** — running it twice is running it once.
* **Both layouts**, because the project that has not migrated is exactly
  the one that needs this.
* **It declines to move files**, and says so with the command that does.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from forge.core.cli.envelope import CommandEnvelope
from forge.core.cli.main import build_parser
from forge.project.migrate import plan_migration, project_files

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foreign" / "simple_pipeline"


def _run(capsys: pytest.CaptureFixture[str], argv: list):
    parser = build_parser()
    code = 0
    try:
        parsed = parser.parse_args(argv)
        parsed.func(parsed)
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    """A copy of a real plugin in the pre-`forge.yml` layout, with its
    schema versions stripped the way a project written before the field
    existed has them."""
    import re

    destination = tmp_path / "passthrough_demo"
    shutil.copytree(REPO_ROOT / "plugins/passthrough_demo", destination)
    for path in project_files(destination):
        text = path.read_text()
        text = re.sub(r"^\s*schema_version:.*\n", "", text, flags=re.M)
        text = re.sub(r"^registry_version:.*\n", "", text, flags=re.M)
        path.write_text(text)
    return destination


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    """A freshly adopted project — already current by construction."""
    from forge.project.adopt import plan_adoption

    destination = tmp_path / "simple_pipeline"
    shutil.copytree(FIXTURE, destination)
    plan_adoption(destination).write()
    return destination


# ── Finding the files ─────────────────────────────────────────────────────

def test_the_legacy_plugin_layout_is_found(legacy: Path) -> None:
    names = {p.name for p in project_files(legacy)}

    assert "design.yml" in names
    assert "modules.yml" in names
    assert "passthrough.interface.yaml" in names
    assert "design.verification.yml" in names


def test_the_forge_yml_layout_is_found(adopted: Path) -> None:
    found = {p.name for p in project_files(adopted)}

    assert "design.yml" in found
    assert "modules.yml" in found
    assert "shaper.interface.yaml" in found


def test_a_plugins_directory_is_walked(tmp_path: Path) -> None:
    """A repository holding several plugins must migrate all of them."""
    shutil.copytree(
        REPO_ROOT / "plugins/passthrough_demo", tmp_path / "plugins" / "one")
    shutil.copytree(
        REPO_ROOT / "plugins/passthrough_demo", tmp_path / "plugins" / "two")

    found = project_files(tmp_path)

    assert sum(1 for p in found if p.name == "modules.yml") == 2


def test_file_discovery_is_stable(legacy: Path) -> None:
    assert project_files(legacy) == project_files(legacy)


# ── Planning ──────────────────────────────────────────────────────────────

def test_a_project_missing_its_schema_versions_needs_migrating(
    legacy: Path,
) -> None:
    plan = plan_migration(legacy)
    ids = {m.id for m in plan.pending}

    assert not plan.up_to_date
    assert ids == {"schema-version"}
    assert len(plan.pending) >= 3


def test_a_freshly_adopted_project_is_already_current(adopted: Path) -> None:
    """Adoption writes current schemas, so migration must find nothing —
    otherwise the two disagree about what FORGE writes today."""
    plan = plan_migration(adopted)

    assert plan.up_to_date
    assert plan.pending == []


def test_planning_writes_nothing(legacy: Path) -> None:
    before = {p: p.read_bytes() for p in project_files(legacy)}

    plan_migration(legacy)

    assert {p: p.read_bytes() for p in project_files(legacy)} == before


def test_a_legacy_partition_label_is_offered_as_coordinates(
    adopted: Path,
) -> None:
    contract = adopted / ".forge/contracts/shaper.interface.yaml"
    contract.write_text(contract.read_text().replace(
        "    shaped:\n", "    shaped:\n      partition: lower_half\n"))

    plan = plan_migration(adopted)

    assert [m.id for m in plan.pending] == ["partition-to-coordinates"]
    assert "shaped" in plan.pending[0].title


def test_two_rules_on_one_file_compose_into_a_single_write(
    adopted: Path,
) -> None:
    """The regression this composition exists for. A contract can both need
    its schema version declared *and* carry a legacy `partition:` label.
    Computing each from what is on disk and writing both in turn means the
    second write silently discards the first — which showed up as a
    migration that was not idempotent, since the discarded change was still
    outstanding on the next run."""
    import re

    contract = adopted / ".forge/contracts/shaper.interface.yaml"
    text = re.sub(r"^\s*schema_version:.*\n", "", contract.read_text(), flags=re.M)
    text = text.replace("    shaped:\n", "    shaped:\n      partition: lower_half\n")
    contract.write_text(text)

    plan = plan_migration(adopted)
    migrations = [m for m in plan.pending if m.path == contract]

    # One migration for the file, carrying both steps.
    assert len(migrations) == 1
    assert set(migrations[0].steps) == {"schema-version", "partition-to-coordinates"}

    plan.apply()
    migrated = contract.read_text()
    assert 'schema_version: "1.0"' in migrated
    assert "coordinates: {partition: lower_half}" in migrated
    assert plan_migration(adopted).up_to_date


def test_a_multi_step_migration_reports_every_step(adopted: Path) -> None:
    import re

    contract = adopted / ".forge/contracts/shaper.interface.yaml"
    text = re.sub(r"^\s*schema_version:.*\n", "", contract.read_text(), flags=re.M)
    contract.write_text(
        text.replace("    shaped:\n", "    shaped:\n      partition: lower_half\n"))

    migration = next(m for m in plan_migration(adopted).pending if m.path == contract)

    assert "declare the interface schema version" in migration.title
    assert "wrap partition as coordinates" in migration.title


def test_only_narrows_to_one_migration(legacy: Path) -> None:
    assert plan_migration(legacy, only=["partition-to-coordinates"]).pending == []
    assert plan_migration(legacy, only=["schema-version"]).pending


def test_a_file_whose_schema_is_unrecognised_is_left_alone(
    adopted: Path,
) -> None:
    stray = adopted / ".forge/project/notes.yml"
    stray.write_text("just: some notes\n")

    plan = plan_migration(adopted)

    assert not any(m.path == stray for m in plan.pending)
    assert stray.read_text() == "just: some notes\n"


def test_an_unparseable_file_does_not_stop_the_rest(adopted: Path) -> None:
    """`forge check` is where a broken file is reported. Migration must not
    become a second, worse validator that refuses to migrate anything."""
    (adopted / ".forge/contracts/shaper.interface.yaml").write_text(
        "ip_interface: [this is not a mapping\n")
    contract = adopted / ".forge/contracts/packer.interface.yaml"
    contract.write_text(contract.read_text().replace(
        "    packet_out:\n", "    packet_out:\n      partition: upper\n"))

    plan = plan_migration(adopted)

    assert [m.path.name for m in plan.pending] == ["packer.interface.yaml"]


# ── Applying ──────────────────────────────────────────────────────────────

def test_apply_writes_exactly_what_the_preview_showed(legacy: Path) -> None:
    plan = plan_migration(legacy)
    previewed = {m.path: m.after for m in plan.pending}

    plan.apply()

    for path, expected in previewed.items():
        assert path.read_text() == expected


def test_migration_is_idempotent(legacy: Path) -> None:
    plan_migration(legacy).apply()

    assert plan_migration(legacy).up_to_date


def test_a_migrated_project_still_validates(
    legacy: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The point of the whole exercise. A migration that leaves the project
    unusable is worse than one that never ran."""
    plan_migration(legacy).apply()

    code, _out, _err = _run(
        capsys, ["topgen", "validate", str(legacy / "forge/designs/design.yml")])

    assert code == 0


def test_migration_delegates_rather_than_reimplementing(legacy: Path) -> None:
    """Byte-identical to what `forge topgen migrate` writes for the same
    file — a second implementation that drifted would be undetectable
    until it corrupted someone's project."""
    from forge.generation.migrate import migrate_schema_version

    design = legacy / "forge/designs/design.yml"
    direct = migrate_schema_version(design, schema_kind="design")

    plan = plan_migration(legacy, only=["schema-version"])
    through_plan = next(m for m in plan.pending if m.path == design)

    assert through_plan.after == direct.new_content


# ── What it declines to do ────────────────────────────────────────────────

def test_a_file_moving_migration_is_reported_not_performed(
    tmp_path: Path,
) -> None:
    """A command someone runs to preview a change must not be one that
    could reorganise their repository."""
    plugin = tmp_path / "legacy_plugin"
    (plugin / "verify").mkdir(parents=True)
    (plugin / "verify" / "bootstrap.py").write_text("# placeholder\n")

    plan = plan_migration(plugin)
    plan.apply()

    assert any(a.id == "migrate-legacy-plugin-layout" for a in plan.manual)
    assert not plan.up_to_date
    # Nothing moved.
    assert (plugin / "verify" / "bootstrap.py").is_file()
    assert not (plugin / "forge").exists()


def test_the_manual_action_names_the_command_that_performs_it(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "legacy_plugin"
    (plugin / "verify").mkdir(parents=True)
    (plugin / "verify" / "bootstrap.py").write_text("# placeholder\n")

    action = plan_migration(plugin).manual[0]

    assert action.command.startswith("forge topgen migrate --kind legacy-plugin-layout")
    assert action.safe is False


# ── Safety ────────────────────────────────────────────────────────────────

def test_a_project_outside_version_control_is_warned_before_writing(
    legacy: Path,
) -> None:
    plan = plan_migration(legacy)

    assert any("not a git repository" in note for note in plan.notes)


def test_a_git_repository_gets_no_such_warning(legacy: Path) -> None:
    (legacy / ".git").mkdir()

    assert plan_migration(legacy).notes == []


def test_a_project_with_nothing_to_do_is_not_warned(adopted: Path) -> None:
    """A safety note about a write that is not going to happen is noise."""
    assert plan_migration(adopted).notes == []


# ── The CLI ───────────────────────────────────────────────────────────────

def test_migrate_previews_by_default(
    capsys: pytest.CaptureFixture[str], legacy: Path,
) -> None:
    before = {p: p.read_bytes() for p in project_files(legacy)}

    code, out, _err = _run(capsys, ["migrate", str(legacy)])

    assert code == 0
    assert "Required changes:" in out
    assert "forge migrate --apply" in out
    assert {p: p.read_bytes() for p in project_files(legacy)} == before


def test_migrate_apply_writes(
    capsys: pytest.CaptureFixture[str], legacy: Path,
) -> None:
    code, out, _err = _run(capsys, ["migrate", str(legacy), "--apply"])

    assert code == 0
    assert "Applied:" in out
    assert 'schema_version: "1.0"' in (legacy / "forge/designs/design.yml").read_text()


def test_migrate_reports_a_current_project_as_current(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    code, out, _err = _run(capsys, ["migrate", str(adopted)])

    assert code == 0
    assert "none — every file already declares" in out


def test_migrate_json_round_trips_through_the_shared_envelope(
    capsys: pytest.CaptureFixture[str], legacy: Path,
) -> None:
    code, out, _err = _run(capsys, ["migrate", str(legacy), "--json"])
    payload = json.loads(out)

    assert code == 0
    envelope = CommandEnvelope.from_dict(payload)
    assert envelope.status == "pass"
    assert payload["metrics"]["dry_run"] is True
    assert payload["metrics"]["up_to_date"] is False
    assert payload["metrics"]["target_versions"]["design"]
    assert payload["metrics"]["migrations"][0]["diff"]


def test_migrate_reports_the_schemas_this_build_writes(
    capsys: pytest.CaptureFixture[str], adopted: Path,
) -> None:
    """So a reader can tell what they are migrating *to*, not just that
    something changed."""
    code, out, _err = _run(capsys, ["migrate", str(adopted)])

    assert code == 0
    assert "This FORGE writes" in out
    assert "forge.yml" in out
