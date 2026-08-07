"""Compatibility alias for :mod:`forge.verification.stale_artifact`.

.. deprecated::
    Import from :mod:`forge.verification.stale_artifact` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.stale_artifact import *  # noqa: F401,F403
