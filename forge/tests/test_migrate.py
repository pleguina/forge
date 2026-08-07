"""
Tests for forge.generation.migrate — the pure migration functions behind
`forge topgen migrate`. CLI-level tests live in
test_topgen_migrate_cli.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.generation.migrate import (
    apply_legacy_plugin_layout,
    apply_rename_verify_contract,
    detect_schema_kind,
    find_legacy_plugin_layout,
    find_legacy_verify_contract_name,
    infer_contract_skeleton,
    migrate_schema_version,
    partition_to_coordinates,
    unified_diff_text,
)


# ─────────────────────────────────────────────────────────────────────────────
# unified_diff_text
# ─────────────────────────────────────────────────────────────────────────────

def test_unified_diff_text_shows_added_line():
    diff = unified_diff_text("a\nb\n", "a\nx\nb\n", Path("f.yml"))
    assert "+x" in diff
    assert "f.yml" in diff


# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema-version insertion
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectSchemaKind:
    def test_design_yml(self):
        assert detect_schema_kind(Path("design.yml")) == "design"

    def test_modules_yml(self):
        assert detect_schema_kind(Path("modules.yml")) == "registry"

    def test_interface_yaml(self):
        assert detect_schema_kind(Path("foo.interface.yaml")) == "interface"

    def test_verify_contract_current_name(self):
        assert detect_schema_kind(Path("design.verification.yml")) == "verify_contract"

    def test_verify_contract_legacy_name(self):
        assert detect_schema_kind(Path("verify.design.yml")) == "verify_contract"

    def test_unrecognized_name_raises(self):
        with pytest.raises(ValueError):
            detect_schema_kind(Path("something.yml"))


class TestMigrateSchemaVersion:
    def test_design_yml_gets_schema_version_inserted(self, tmp_path):
        p = tmp_path / "design.yml"
        p.write_text("# header comment\npart: xcvu13p\nclock_period: 4.0\n")
        result = migrate_schema_version(p)
        assert result.changed
        assert 'schema_version: "1.0"' in result.new_content
        assert "# header comment" in result.new_content  # comment preserved
        assert "part: xcvu13p" in result.new_content

    def test_already_declared_is_a_noop(self, tmp_path):
        p = tmp_path / "design.yml"
        p.write_text('schema_version: "1.0"\npart: xcvu13p\n')
        result = migrate_schema_version(p)
        assert not result.changed
        assert result.diff == ""

    def test_registry_gets_registry_version_inserted(self, tmp_path):
        p = tmp_path / "modules.yml"
        p.write_text("modules: []\n")
        result = migrate_schema_version(p)
        assert result.changed
        assert 'registry_version: "1.0"' in result.new_content

    def test_interface_contract_inserts_nested_under_ip_interface(self, tmp_path):
        p = tmp_path / "m.interface.yaml"
        p.write_text(
            "# a comment\n"
            "ip_interface:\n"
            "  module_name: m\n"
            "  roles: {}\n"
        )
        result = migrate_schema_version(p)
        assert result.changed
        lines = result.new_content.splitlines()
        assert lines[1] == "ip_interface:"
        assert lines[2].strip() == 'schema_version: "1.0"'
        # Preserves the existing 2-space indentation convention.
        assert lines[2].startswith("  ")

    def test_interface_contract_already_declared_is_a_noop(self, tmp_path):
        p = tmp_path / "m.interface.yaml"
        p.write_text('ip_interface:\n  schema_version: "1.0"\n  module_name: m\n')
        result = migrate_schema_version(p)
        assert not result.changed

    def test_verify_contract_gets_schema_version_inserted(self, tmp_path):
        p = tmp_path / "design.verification.yml"
        p.write_text("plugin: demo\ndatasets: {}\nflows: []\n")
        result = migrate_schema_version(p)
        assert result.changed
        assert 'schema_version: "1.0"' in result.new_content

    def test_explicit_schema_kind_overrides_filename_detection(self, tmp_path):
        p = tmp_path / "oddly_named.yml"
        p.write_text("part: xcvu13p\n")
        result = migrate_schema_version(p, schema_kind="design")
        assert result.changed
        assert 'schema_version: "1.0"' in result.new_content

    def test_unrecognized_filename_without_override_raises(self, tmp_path):
        p = tmp_path / "oddly_named.yml"
        p.write_text("part: xcvu13p\n")
        with pytest.raises(ValueError):
            migrate_schema_version(p)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Partition strings -> structured coordinates
# ─────────────────────────────────────────────────────────────────────────────

_CONTRACT_TEXT = """\
ip_interface:
  module_name: m
  roles:
    raw_hit:
      raw_port: raw_hit
      direction: input
      width: 32
      partition: lower_pair
    other_role:
      raw_port: other
      direction: input
      width: 1
      coordinates: {sector: 2}
    third_role:
      raw_port: third
      direction: input
      width: 1
      partition: upper_pair
"""


class TestPartitionToCoordinates:
    def test_wraps_single_eligible_role(self):
        new_text, changed = partition_to_coordinates(_CONTRACT_TEXT, axis="label", role="raw_hit")
        assert changed == ["raw_hit"]
        assert "coordinates: {label: lower_pair}" in new_text
        assert "partition: lower_pair" not in new_text
        # Untouched roles keep their original lines.
        assert "coordinates: {sector: 2}" in new_text
        assert "partition: upper_pair" in new_text

    def test_migrates_all_eligible_roles_by_default(self):
        new_text, changed = partition_to_coordinates(_CONTRACT_TEXT, axis="label")
        assert sorted(changed) == ["raw_hit", "third_role"]
        assert "partition:" not in new_text
        assert "coordinates: {label: lower_pair}" in new_text
        assert "coordinates: {label: upper_pair}" in new_text

    def test_role_already_having_coordinates_is_not_touched_by_default(self):
        new_text, changed = partition_to_coordinates(_CONTRACT_TEXT, axis="label")
        assert "other_role" not in changed
        assert "coordinates: {sector: 2}" in new_text

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError, match="not found"):
            partition_to_coordinates(_CONTRACT_TEXT, axis="label", role="nonexistent")

    def test_ineligible_role_raises(self):
        with pytest.raises(ValueError, match="not eligible"):
            partition_to_coordinates(_CONTRACT_TEXT, axis="label", role="other_role")

    def test_no_eligible_roles_is_a_noop(self):
        text = "ip_interface:\n  roles:\n    r:\n      raw_port: p\n"
        new_text, changed = partition_to_coordinates(text, axis="label")
        assert changed == []
        assert new_text == text


# ─────────────────────────────────────────────────────────────────────────────
# 3. Legacy plugin layout
# ─────────────────────────────────────────────────────────────────────────────

_FW_PYTHON_BOOTSTRAP = (
    "import sys\n"
    "# _FW_PYTHON path hack\n"
    "sys.path.insert(0, \"framework/verify/python\")\n"
    "print('hello')\n"
)


class TestLegacyPluginLayout:
    def test_detects_move_and_fw_python_block(self, tmp_path):
        (tmp_path / "verify").mkdir()
        (tmp_path / "verify" / "bootstrap.py").write_text(_FW_PYTHON_BOOTSTRAP)

        issue = find_legacy_plugin_layout(tmp_path)
        assert issue.move_needed
        assert issue.fw_python_files == [tmp_path / "verify" / "bootstrap.py"]
        assert issue.manual_files == []
        assert not issue.is_clean()

    def test_clean_layout_reports_nothing(self, tmp_path):
        (tmp_path / "forge" / "verify").mkdir(parents=True)
        (tmp_path / "forge" / "verify" / "bootstrap.py").write_text("import sys\nprint('ok')\n")

        issue = find_legacy_plugin_layout(tmp_path)
        assert issue.is_clean()

    def test_apply_moves_directory_and_strips_fw_python_block(self, tmp_path):
        (tmp_path / "verify").mkdir()
        (tmp_path / "verify" / "bootstrap.py").write_text(_FW_PYTHON_BOOTSTRAP)

        issue = find_legacy_plugin_layout(tmp_path)
        actions = apply_legacy_plugin_layout(issue)

        assert not (tmp_path / "verify").exists()
        new_file = tmp_path / "forge" / "verify" / "bootstrap.py"
        assert new_file.exists()
        content = new_file.read_text()
        assert "_FW_PYTHON" not in content
        assert "sys.path.insert" not in content
        assert "print('hello')" in content  # unrelated code preserved
        assert len(actions) == 2

    def test_ambiguous_fw_python_marker_is_flagged_for_manual_removal(self, tmp_path):
        (tmp_path / "verify").mkdir()
        # The marker line and the sys.path line are not contiguous — the
        # migration must refuse to guess rather than delete surrounding code.
        (tmp_path / "verify" / "gen_stimulus.py").write_text(
            "import sys\n"
            "# _FW_PYTHON\n"
            "print('unrelated line in between')\n"
            "sys.path.insert(0, \"framework/verify/python\")\n"
        )
        issue = find_legacy_plugin_layout(tmp_path)
        assert issue.manual_files == [tmp_path / "verify" / "gen_stimulus.py"]
        assert issue.fw_python_files == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Deprecated verify.design.yml -> design.verification.yml
# ─────────────────────────────────────────────────────────────────────────────

class TestRenameVerifyContract:
    def test_detects_legacy_name(self, tmp_path):
        verify_dir = tmp_path / "forge" / "verify"
        verify_dir.mkdir(parents=True)
        (verify_dir / "verify.design.yml").write_text("plugin: demo\n")

        legacy = find_legacy_verify_contract_name(tmp_path)
        assert legacy == verify_dir / "verify.design.yml"

    def test_current_name_already_present_is_a_noop(self, tmp_path):
        verify_dir = tmp_path / "forge" / "verify"
        verify_dir.mkdir(parents=True)
        (verify_dir / "design.verification.yml").write_text("plugin: demo\n")

        assert find_legacy_verify_contract_name(tmp_path) is None

    def test_apply_renames_file(self, tmp_path):
        verify_dir = tmp_path / "forge" / "verify"
        verify_dir.mkdir(parents=True)
        legacy = verify_dir / "verify.design.yml"
        legacy.write_text("plugin: demo\n")

        new_path = apply_rename_verify_contract(legacy)

        assert new_path == verify_dir / "design.verification.yml"
        assert new_path.exists()
        assert not legacy.exists()
        assert new_path.read_text() == "plugin: demo\n"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Compatibility-mode contract inference
# ─────────────────────────────────────────────────────────────────────────────

class TestInferContractSkeleton:
    def test_infers_clock_and_reset(self):
        entry = {"ports": [
            {"name": "ap_clk", "direction": "IN", "width": 1},
            {"name": "ap_rst", "direction": "IN", "width": 1},
            {"name": "data_in", "direction": "IN", "width": 8},
        ]}
        skeleton = infer_contract_skeleton("mymod", entry)
        assert "clock_primary:" in skeleton
        assert "raw_port: ap_clk" in skeleton
        assert "reset_primary:" in skeleton
        assert "raw_port: ap_rst" in skeleton
        # Data port is never assigned a role — only listed as a TODO.
        assert "data_in:" not in skeleton
        assert "# TODO" in skeleton
        assert "data_in (direction=IN, width=8)" in skeleton

    def test_no_clock_or_reset_found_omits_those_roles(self):
        entry = {"ports": [{"name": "weird_port", "direction": "IN", "width": 4}]}
        skeleton = infer_contract_skeleton("mymod", entry)
        assert "clock_primary" not in skeleton
        assert "reset_primary" not in skeleton
        assert "weird_port" in skeleton

    def test_output_is_valid_yaml(self):
        import yaml
        entry = {"ports": [
            {"name": "clk", "direction": "IN", "width": 1},
            {"name": "rst_n", "direction": "IN", "width": 1},
        ]}
        skeleton = infer_contract_skeleton("mymod", entry)
        parsed = yaml.safe_load(skeleton)
        assert parsed["ip_interface"]["module_name"] == "mymod"
        assert "clock_primary" in parsed["ip_interface"]["roles"]
        assert "reset_primary" in parsed["ip_interface"]["roles"]
