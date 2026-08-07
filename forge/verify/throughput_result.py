"""Compatibility alias for :mod:`forge.verification.throughput_result`.

.. deprecated::
    Import from :mod:`forge.verification.throughput_result` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.throughput_result import *  # noqa: F401,F403
