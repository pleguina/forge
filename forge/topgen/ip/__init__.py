"""forge.topgen.ip — deprecated alias for :mod:`forge.contracts`.

.. deprecated::
    Import from :mod:`forge.contracts` instead. `forge.contracts` (and
    `forge.contracts.matcher`, `.cdc`, `.contract_loader`, etc. — the full
    real module tree) supersedes this path. Unlike
    `forge.verify`/`forge.analyze`, this alias is on a deprecation
    timer, not kept permanently: `forge.topgen.ip` is part of the frozen
    public API (docs/reference/public-python-api.md), so it must keep
    working for at least one minor release, but is not promised forever.
    See docs/development/adr/0005-package-and-cli-naming.md.
"""
from warnings import warn as _warn

_warn(
    "forge.topgen.ip is deprecated; import from forge.contracts instead.",
    DeprecationWarning,
    stacklevel=2,
)

from forge.contracts import *  # noqa: F401,F403
from forge.contracts import __all__  # noqa: F401
