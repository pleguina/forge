"""Compatibility alias for :mod:`forge.verification.golden_model`.

.. deprecated::
    Import from :mod:`forge.verification.golden_model` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.golden_model import *  # noqa: F401,F403
