# Extension APIs

FORGE's stated design goal is to carry no hardcoded detector- or
algorithm-specific assumptions in its core (`forge/`, excluding
`forge/tests/` fixtures) — see `CONTRIBUTING.md`'s "Keeping FORGE core
agnostic" section. This page explains the two concrete extension points
that make that possible, and the mechanism that keeps it true over time
rather than as an unenforced aspiration.

## `detector_input_roles`: overriding an endpoint-role naming convention

`forge.integration.io_resolver.resolve()` validates a plugin's
`detector_io.yml` against an imported `FrameworkImport` — checking, among
other things, that each detector input's endpoint role is compatible with
its declared detector type. FORGE places no restriction on detector type
names or trigger-output role names themselves — those are entirely
plugin- and provider-defined vocabulary. The only convention it applies
*by default* is that a detector-input endpoint role follows the pattern
`detector_input_<type>` (lower-cased), matching how upstream endpoint
manifests already tend to name them.

A plugin whose upstream provider uses a different naming convention
doesn't have to work around this default or fork the resolver — `resolve()`
accepts an explicit `detector_input_roles: dict[str, set]` parameter that
overrides the default convention entirely, keyed by upper-cased detector
type. A separate `trigger_output_roles` parameter is similarly optional:
FORGE has no built-in notion of what a trigger-output role *should* be
named, so when it's omitted, any tx-direction role is accepted with no
name check at all. This is the general pattern the parameter exists to
demonstrate: a sensible, documented default that a plugin can override
explicitly, rather than a hardcoded assumption a plugin has to route
around.

## `reference_period_ns`: overriding an implicit timing default

`forge.contracts.config.DesignConfig.reference_period_ns` is the external
synchronous reference period (in nanoseconds) that generated testbench
timing parameters — batches-per-event, counter modulo, and similar — are
derived against. When a `design.yml` doesn't declare one, FORGE defaults
to 25.0 ns (the LHC's 40 MHz bunch-crossing period), simply because that
is FORGE's own origin domain and a value has to exist somewhere. A plugin
targeting a different accelerator, or no accelerator at all, declares its
own `reference_period_ns` in `design.yml` and the default never applies.
The value itself is never assumed anywhere else in `forge/` core beyond
this one, overridable field.

## The enforcement mechanism, not just a policy statement

Both patterns above only mean something if `forge/` core doesn't quietly
grow a second, hardcoded assumption alongside them. `ci/agnosticism_check.sh`
is the real, running guard against that: it greps `forge/core`,
`forge/topgen`, `forge/hls`, `forge/verify`, `forge/analysis`, and
`forge/integration` (deliberately excluding `plugins/` — plugins are
expected to use domain-specific vocabulary freely, and `forge/tests/`,
whose fixtures intentionally exercise one concrete example without that
implying the mechanism itself is hardcoded) for a blocklist of
whole-word, case-sensitive CMS/OMTF-specific tokens
(`DT`/`CSC`/`RPC`/`GMT`/`BMT`/`OMTF`), and fails CI with `file:line`
detail if it finds one. It runs as a real step in
`CONTRIBUTING.md`'s "Running things locally before you push" checklist
and in CI — this is an enforced regression guard, not an unenforced
convention documented once and left to erode. Its own header comment is
explicit that it's a best-effort, real-but-imprecise guard (a determined
author could still write something it can't catch) — its job is catching
the easy, common regressions, not proving agnosticism formally.

## What this doesn't cover

These two parameters are the extension points `CONTRIBUTING.md` names as
*the* pattern — they're illustrative of the discipline, not an exhaustive
list of every configurable field in FORGE. Interface contracts, topology
wiring patterns, and verification datasets are all plugin-authored
configuration in their own right (see
[Authoring topology contracts](../how-to/author-topology-contracts.md)),
just not "extension APIs" in the override-a-hardcoded-default sense this
page is about.
