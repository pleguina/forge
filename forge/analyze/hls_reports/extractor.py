"""Compatibility alias for :mod:`forge.analysis.hls_reports.extractor`.

.. deprecated::
    Import from :mod:`forge.analysis.hls_reports.extractor` instead. This alias is
    kept working indefinitely (not on a deprecation timer) — existing
    plugin tool code may import ``forge.analyze.*`` directly and FORGE
    doesn't control when those get updated.
"""
from forge.analysis.hls_reports.extractor import *  # noqa: F401,F403
