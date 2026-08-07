"""Compatibility alias for :mod:`forge.verification.gen_sim`.

.. deprecated::
    Import from :mod:`forge.verification.gen_sim` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin code may import ``forge.verify.*`` directly and FORGE doesn't
    control when those get updated.
"""
from forge.verification.gen_sim import *  # noqa: F401,F403
