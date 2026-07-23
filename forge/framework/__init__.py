"""
FORGE External Framework Support

This package provides framework-agnostic support for importing a hardware
framework's ABI and endpoint manifests, resolving detector I/O mappings, and
validating the combined contract.

Supported providers:
  - blobfish  (reads payload_abi.json + payload_endpoints.json)

The provider model is intentionally open: any framework that can produce an
ABI manifest and an endpoint manifest in the expected schema can be supported
by adding a loader under forge.framework.providers.<name>.
"""
