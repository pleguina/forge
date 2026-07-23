"""
FORGE External Framework Support

This package provides framework-agnostic support for importing a hardware
framework's ABI and endpoint manifests, resolving detector I/O mappings, and
validating the combined contract.

The provider model is intentionally open: `forge.framework.importer.load()`
accepts any provider name and parses the same payload_abi.json /
payload_endpoints.json schema regardless of which external framework
produced it — "blobfish" is simply the provider FORGE has been exercised
against so far, not an enforced allow-list.

Similarly, detector type names and trigger-output role names are not
hardcoded: `forge.framework.io_resolver.resolve()` derives detector input
role compatibility from a generic "detector_input_<type>" naming
convention (overridable via its `detector_input_roles` parameter), and only
validates trigger output roles when the caller explicitly supplies a
`trigger_output_roles` allow-list.
"""
