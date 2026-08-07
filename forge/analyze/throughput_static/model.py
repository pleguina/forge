"""Compatibility alias for :mod:`forge.analysis.throughput_static.model`.

.. deprecated::
    Import from :mod:`forge.analysis.throughput_static.model` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin tool code may import ``forge.analyze.*`` directly and FORGE
    doesn't control when those get updated.
"""
from forge.analysis.throughput_static.model import *  # noqa: F401,F403
