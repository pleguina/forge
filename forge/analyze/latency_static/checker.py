"""Compatibility alias for :mod:`forge.analysis.latency_static.checker`.

.. deprecated::
    Import from :mod:`forge.analysis.latency_static.checker` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin tool code may import ``forge.analyze.*`` directly and FORGE
    doesn't control when those get updated.
"""
from forge.analysis.latency_static.checker import *  # noqa: F401,F403
