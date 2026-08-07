"""Compatibility alias for :mod:`forge.verification.stimulus_helpers`.

.. deprecated::
    Import from :mod:`forge.verification.stimulus_helpers` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.stimulus_helpers import *  # noqa: F401,F403
