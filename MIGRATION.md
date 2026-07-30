# Migration Notes

## ARC → FORGE rename

The framework was renamed from **ARC** ("Algorithm Runtime & Contract") to
**FORGE** ("Framework for Orchestrated RTL Generation & Evaluation") to avoid
a naming collision with existing CMS/WLCG grid-computing usage of "ARC"
(Advanced Resource Connector) as well as internal usage of "ARC" for the
Analysis Review Comitee. This was a breaking rename with **no
compatibility shim** — the old `arc` command/package/paths do not work
anymore and will not be restored.

If you have an existing checkout, CI pipeline, or downstream repository
(e.g. a plugin repo) that references the old names, you need to update:

| Old | New |
|---|---|
| `arc` CLI command | `forge` |
| `arc` Python package (`arc.core`, `arc.topgen`, `arc.hls`, `arc.verify`, `arc.analyze`) | `forge` (`forge.core`, `forge.topgen`, `forge.hls`, `forge.verify`, `forge.analyze`) |
| `pip install -e arc/` | `pip install -e forge/` |
| `arc/` top-level package directory | `forge/` |
| `plugins/<plugin>/arc/` capsule directory | `plugins/<plugin>/forge/` |
| CMake project `arc_framework` | `forge_framework` |
| CI: `.arc_*` YAML anchors, `ARC_*` env vars | `.forge_*`, `FORGE_*` |
| `fw_verify` (legacy package name, predates the `arc` rename) | `forge.verify` (module), `forge verify` (CLI) |
| `topgen` (legacy standalone command, predates the `arc` rename) | `forge topgen` |

The GitLab project/remote itself
(`gitlab.cern.ch/p2u-omtf-ops/arc-framework`) was **not** renamed — only the
code, package, CLI, and branding inside it. Clone URLs and the repository
name are unaffected.

### If your plugin repo includes `ci/plugin-consumer.yml`

The reusable CI templates in that file were also updated to install `forge`
and use `forge topgen`/`forge verify`/`forge core` commands. If your
`.gitlab-ci.yml` extends `.forge_plugin_setup`, `.forge_contract_validate`,
or the old `.forge_fw_verify_doctor` (now `.forge_verify_doctor`), re-check
it against the current `ci/plugin-consumer.yml` after pulling this change —
the old PyPI package names it referenced (`topgen`, `fw-verify`) never
actually existed, so any pipeline depending on them was already broken.

### If you scaffolded a plugin with `arc verify init-plugin` (or an older
`forge verify init-plugin`) before this fix

Plugins scaffolded before the `forge/verify/__main__.py` init-plugin fix
were created under `<plugin>/verify/` instead of `<plugin>/forge/verify/`,
and their generated `bootstrap.py`/`gen_stimulus.py` contained a dead
`sys.path` hack pointing at a `framework/verify/python` directory that
hasn't existed since the pre-`arc` package layout. Move the plugin's
`verify/` directory to `forge/verify/`, and remove the `_FW_PYTHON`
sys.path block from `bootstrap.py`/`gen_stimulus.py` — the framework now
just needs to be pip-installed (`pip install -e forge/`) for
`from forge.verify.X import Y` to work directly.

This can now be automated: `forge topgen migrate --kind legacy-plugin-layout
--plugin-root <plugin> [--dry-run]`. See
`docs/development/MIGRATION_TOOLING.md` for this and four other migration
helpers (schema-version insertion, partition-string-to-coordinates,
the deprecated `verify.design.yml` filename, and compatibility-mode
contract inference).
