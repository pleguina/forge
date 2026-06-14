# Framework Support Classification

This inventory defines the support status for maintained surfaces referenced by the active reading path.

## Support Classes

| Class | Meaning |
|---|---|
| Public framework API | Implemented, documented, intentional, and verification-backed framework surface |
| Supported proof consumer | Maintained non-OMTF consumer used to prove the public framework path and release-readiness story |
| Supported optional tooling | Supported Layer 2 tooling that is optional for basic framework adoption |
| Supported convenience only | Maintained helper surface for repo-local productivity; not the primary contract |
| Downstream consumer only | Plugin-specific surface that demonstrates one consumer pattern, not framework API |
| Internal / not supported | Maintainer-only or repo-local implementation surface; external consumers must not depend on it |
| Historical / archived | Retained for history only; not part of the active route or supported contract |

## Inventory

| Surface | Class | Why |
|---|---|---|
| `arc` CLI (`arc topgen validate`, `arc topgen gen-top`, `arc topgen ip-summary`, `arc topgen match-ports`, `arc topgen unpack-ips`, `arc core resources`, `arc core verify-contract`) | Public framework API | primary installed framework entrypoint |
| `framework::verif_core` and exported headers documented in `FRAMEWORK_CORE_INTERFACE.md` | Public framework API | installable verification package boundary |
| `framework/FRAMEWORK_CORE_INTERFACE.md` | Public framework API | defines Layer 1 contract |
| `framework/MINIMAL_CONSUMER_QUICKSTART.md` | Public framework API | shortest supported adoption path |
| `docs/FRAMEWORK_PLUGIN_AGNOSTICITY_AUDIT_2026-04-20.md` | Supported convenience only | maintained boundary-analysis and export-readiness note |
| `docs/FRAMEWORK_EXPORT_MANIFEST.md` | Supported convenience only | maintained extraction inventory for the planned framework repository split |
| downstream plugin guides | Downstream consumer only | plugin-specific integration guides belong in plugin repositories, not ARC core |
| `plugins/trigger_demo/README.md` | Supported proof consumer | multi-flow non-OMTF consumer documentation |
| `plugins/trigger_demo/verify/` | Supported proof consumer | supported proof that topology contracts plus framework-owned csim/xsim/full-chip flows work for a non-OMTF plugin |
| `arc hls gen-tcl` | Supported optional tooling | preferred Layer 2 TCL-generation interface |
| `arc hls run` | Supported optional tooling | preferred Layer 2 batch execution interface |
| `framework/hls/parallel_hls.sh` | Supported optional tooling | compatibility/backend batch entrypoint behind `arc hls run` |
| `framework/hls/generate_hls_tcl.py` | Supported optional tooling | compatibility/backend entrypoint behind `arc hls gen-tcl` |
| `framework/hls/extract_hls_metrics.py` | Supported optional tooling | structured metrics extraction surface |
| `framework/FRAMEWORK_TOOLING_INTERFACE.md` | Supported optional tooling | defines Layer 2 contract |
| `framework/validate_framework.sh` | Supported convenience only | framework standalone validation helper for export-readiness checks |
| `tests/external_consumer_proof/` | Supported convenience only | maintained proof that an external consumer can use the installed framework interfaces |
| `ci/verify_framework_release.sh` | Supported convenience only | framework-only release gate that excludes OMTF as a required dependency |
| `ci/fresh_user_check.sh` | Supported convenience only | framework package-install and onboarding validation path |
| `ci/fresh_user_path.sh` | Supported convenience only | framework fresh-user path validation from package install through consumer proof |
| downstream compatibility gates | Downstream consumer only | plugin-specific compatibility gates belong in plugin repositories or external integration CI |
| `ci/split_boundary_smoke.sh` | Supported convenience only | maintainer smoke gate spanning framework export checks plus downstream compatibility |
| `Makefile.hls` | Supported convenience only | repo-root facade over Layer 2 tooling |
| `framework/hls/generate_reports.sh` | Supported convenience only | convenience wrapper around metrics tooling |
| `framework/hls/visualize_hls_pipeline.py` | Supported convenience only | optional visualization helper |
| `plugins/<plugin>/` local mounts | Downstream consumer only | optional local mounts for development; not committed ARC core |
| plugin-specific regression scripts | Downstream consumer only | plugin-owned verification gates |
| `framework/hls/generate_algorithm_top.sh` | Internal / not supported | monorepo convenience wrapper only |
| `framework/topgen/algo_top_gen/` Python internals | Internal / not supported | implementation detail behind installed CLI |
| `framework/proof/` | Internal / not supported | validation proof material, not consumer API |
| `build/`, `build_hls/`, `build_targeted/`, `out/`, `ips/`, `ip_packages/` | Internal / not supported | generated working directories |
| `docs/archive/REFACTORING_HISTORY.md` | Historical / archived | retained summary of superseded refactor plans and summaries |
| `docs/archive/LEGACY_SCHEMA_CONTRACT.md` | Historical / archived | retained summary of the superseded schema and BlobFiSH contract story |
| `docs/archive/DELAY_IMPLEMENTATION_GUIDE.md` | Historical / archived | retained implementation-history note, not current contract guidance |

## Enforcement Rule

If a surface does not satisfy its class definition, it must be reclassified, implemented, demoted, archived, or removed in the same remediation program.