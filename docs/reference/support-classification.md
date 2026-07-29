# Support Classification

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
| `forge` CLI (`forge topgen validate`, `forge topgen gen-top`, `forge topgen ip-summary`, `forge topgen match-ports`, `forge topgen unpack-ips`, `forge core resources`, `forge core verify-contract`) | Public framework API | primary installed framework entrypoint |
| `forge.verify` Python package (`forge/verify/`) | Public framework API | installed verification package boundary; `verify/src/xsi_loader.cpp` at repo root is a standalone XSI loader, not a built/linked library — plugins own their own C++ verify headers/sources under `plugins/<plugin>/forge/verify/include` and `.../src` |
| `docs/concepts/core-interface.md` | Public framework API | defines Layer 1 contract |
| `docs/getting-started/quickstart.md` | Public framework API | shortest supported adoption path |
| downstream plugin guides | Downstream consumer only | plugin-specific integration guides belong in plugin repositories, not FORGE core |
| `plugins/trigger_demo/README.md` | Supported proof consumer | multi-flow non-OMTF consumer documentation |
| `plugins/trigger_demo/forge/verify/` | Supported proof consumer | supported proof that topology contracts plus framework-owned csim/xsim/full-chip flows work for a non-OMTF plugin |
| `forge hls gen-tcl` | Supported optional tooling | preferred Layer 2 TCL-generation interface |
| `forge hls run` | Supported optional tooling | preferred Layer 2 batch execution interface |
| `forge/hls/generate_hls_tcl.py` | Supported optional tooling | implementation behind `forge hls gen-tcl` |
| `forge/hls/extract_hls_metrics.py` | Supported optional tooling | structured metrics extraction surface |
| `docs/concepts/tooling-interface.md` | Supported optional tooling | defines Layer 2 contract |
| `run_trigger_demo.sh` | Supported proof consumer | the actual hardware-toolchain-dependent release gate (`ci/framework-release.yml`); validate → gen-top → verify generate → doctor → HLS csim/synth → all 9 verification flows |
| `ci/fresh_user_check.sh` | Supported convenience only | framework package-install and onboarding validation path; also the agnosticism regression guard (scaffolds and doctors a generically-named plugin via `init-plugin`) |
| `ci/agnosticism_check.sh` | Supported convenience only | source-level guard against hardcoded CMS/OMTF vocabulary creeping back into `forge/` core |
| downstream compatibility gates | Downstream consumer only | plugin-specific compatibility gates belong in plugin repositories or external integration CI |
| `forge/hls/visualize_hls_pipeline.py` | Supported convenience only | optional visualization helper |
| `plugins/<plugin>/` local mounts | Downstream consumer only | optional local mounts for development; not committed FORGE core |
| plugin-specific regression scripts | Downstream consumer only | plugin-owned verification gates |
| `forge/topgen/` Python internals | Internal / not supported | implementation detail behind installed CLI |
| `build/`, `build_hls/`, `build_targeted/`, `out/`, `ips/`, `ip_packages/` | Internal / not supported | generated working directories |

## Enforcement Rule

If a surface does not satisfy its class definition, it must be reclassified, implemented, demoted, archived, or removed in the same remediation program.
