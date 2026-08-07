"""Compatibility alias for :mod:`forge.analysis.design_explorer.escaping`.

.. deprecated::
    Import from :mod:`forge.analysis.design_explorer.escaping` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin tool code may import ``forge.analyze.*`` directly and FORGE
    doesn't control when those get updated.
"""
from forge.analysis.design_explorer.escaping import *  # noqa: F401,F403
