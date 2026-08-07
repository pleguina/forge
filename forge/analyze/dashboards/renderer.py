"""Compatibility alias for :mod:`forge.analysis.dashboards.renderer`.

.. deprecated::
    Import from :mod:`forge.analysis.dashboards.renderer` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin tool code may import ``forge.analyze.*`` directly and FORGE
    doesn't control when those get updated.
"""
from forge.analysis.dashboards.renderer import *  # noqa: F401,F403
