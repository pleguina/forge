"""Compatibility alias for :mod:`forge.analysis.result_plots.engine`.

.. deprecated::
    Import from :mod:`forge.analysis.result_plots.engine` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin tool code may import ``forge.analyze.*`` directly and FORGE
    doesn't control when those get updated.
"""
from forge.analysis.result_plots.engine import *  # noqa: F401,F403
