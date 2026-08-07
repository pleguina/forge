"""Compatibility alias for :mod:`forge.verification.release_check`.

.. deprecated::
    Import from :mod:`forge.verification.release_check` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.release_check import *  # noqa: F401,F403
