"""Compatibility alias for :mod:`forge.verification.execution_stage`.

.. deprecated::
    Import from :mod:`forge.verification.execution_stage` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.execution_stage import *  # noqa: F401,F403
