"""Compatibility alias for :mod:`forge.verification.subprocess_wrapper`.

.. deprecated::
    Import from :mod:`forge.verification.subprocess_wrapper` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.subprocess_wrapper import *  # noqa: F401,F403
