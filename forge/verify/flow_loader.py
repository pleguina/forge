"""Compatibility alias for :mod:`forge.verification.flow_loader`.

.. deprecated::
    Import from :mod:`forge.verification.flow_loader` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.flow_loader import *  # noqa: F401,F403
