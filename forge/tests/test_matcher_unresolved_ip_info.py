"""
Regression test for a crash found while building the canonical IR
(forge/ir/build.py): forge.contracts.matcher.auto_match_ports's
heuristic global-net (clock/reset/control-signal) scanner assumed every
module resolved to a non-None ip_info entry and crashed with a bare
``TypeError: 'NoneType' object is not subscriptable`` whenever a module had
no resolvable IP/RTL port metadata (e.g. missing HLS build artifacts, no
interface contract). That's a real scenario for any partially-built design,
not something exotic to the IR.

Fixed by routing every "which ports does module X have" lookup in the
global-net section through ``_mod_ports``/``_mod_port_dicts``, which return
an empty result (plus a one-time ``report.warnings`` entry) instead of
subscripting a ``None`` value.
"""

from __future__ import annotations

from pathlib import Path

from forge.contracts.config import DesignConfig
from forge.contracts.matcher import auto_match_ports


def _write_single_module_design(tmp_path: Path, *, name: str = "orphan") -> Path:
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        f"  - name: {name}\n"
        f"    top: {name}_top\n"
        "    src: []\n"
        "    kind: rtl\n"
        "    instances: 1\n"
    )
    return design_yml


def test_matcher_handles_unresolved_module_without_crashing(tmp_path):
    cfg = DesignConfig.load_relaxed(_write_single_module_design(tmp_path))

    # ip_info entry is None — the same shape forge.contracts.parser.collect_all
    # produces for a module it couldn't find a component.xml or HDL source for.
    conn_map, global_nets, report = auto_match_ports(cfg, {"orphan": None})

    assert conn_map == {}
    assert global_nets == {}
    assert any("orphan" in w and "skipped for global-net" in w for w in report.warnings)


def test_matcher_handles_missing_ip_info_key_without_crashing(tmp_path):
    """Same as above, but the module's key is entirely absent from ip_info
    (not even present with a None value) — a slightly different but
    equally real degraded case."""
    cfg = DesignConfig.load_relaxed(_write_single_module_design(tmp_path))

    conn_map, global_nets, report = auto_match_ports(cfg, {})

    assert conn_map == {}
    assert global_nets == {}
    assert any("orphan" in w for w in report.warnings)


def test_matcher_resolved_and_unresolved_modules_mixed(tmp_path):
    """One resolved, one unresolved module in the same design: the resolved
    module's clock/reset must still be wired normally; the unresolved one
    must be skipped with a warning, not abort the whole run."""
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: healthy\n"
        "    top: healthy_top\n"
        "    src: []\n"
        "    kind: rtl\n"
        "    instances: 1\n"
        "  - name: orphan\n"
        "    top: orphan_top\n"
        "    src: []\n"
        "    kind: rtl\n"
        "    instances: 1\n"
    )
    cfg = DesignConfig.load_relaxed(design_yml)
    ip_info = {
        "healthy": {"ports": [
            {"name": "ap_clk", "direction": "IN", "width": 1, "type": "wire"},
            {"name": "ap_rst", "direction": "IN", "width": 1, "type": "wire"},
        ]},
        "orphan": None,
    }

    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)

    assert "ap_clk" in global_nets
    assert ("healthy", "ap_clk") in global_nets["ap_clk"]
    assert not any(inst == "orphan" for inst, _ in global_nets.get("ap_clk", []))
    assert any("orphan" in w for w in report.warnings)
