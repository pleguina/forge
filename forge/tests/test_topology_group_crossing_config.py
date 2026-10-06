"""topology_groups accept delay_cycles/boundary like connections do."""
from __future__ import annotations

import pytest

from forge.contracts.config import DesignConfig

_YAML = """\
schema_version: "1.0"
part: xcvu13p
clock_period: 4.0
block_protocol: none

modules:
  - name: src
    top: src_top
    src: []
  - name: dst
    top: dst_top
    src: []

topology_groups:
  - name: tg
    family: wk
    from: src
    to: dst
    role_pairs: [[a_out, b_in]]
{extra}
"""


def _load(tmp_path, extra):
    path = tmp_path / "design.yml"
    path.write_text(_YAML.format(extra=extra))
    return DesignConfig.load(path)


def test_delay_and_boundary_are_parsed(tmp_path):
    cfg = _load(tmp_path, "    delay_cycles: 7\n    boundary: in_to_front")
    tg = cfg.topology_groups[0]
    assert (tg.delay_cycles, tg.boundary) == (7, "in_to_front")


def test_defaults_are_no_delay(tmp_path):
    tg = _load(tmp_path, "").topology_groups[0]
    assert (tg.delay_cycles, tg.boundary) == (0, None)


def test_boundary_without_delay_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="boundary"):
        _load(tmp_path, "    boundary: in_to_front")


def test_negative_delay_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="delay_cycles"):
        _load(tmp_path, "    delay_cycles: -1")
