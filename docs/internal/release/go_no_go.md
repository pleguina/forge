# FORGE — Stable Release Go/No-Go

Per `docs/plan/FORGE_framework_freeze_and_public_release_plan.md` §15.
Evaluated against the real, current state of `rename/forge` (latest
commit at time of writing: `dbaf17b`), not the plan's aspirational text.

## Later update: the public-host decision is made

The one real blocker below (`<org>` placeholder) is resolved: the
canonical public host is `https://github.com/pleguina/forge`. All 4+
files referencing the placeholder were updated (see
`CHANGELOG.md`'s "Public host decided" entry), plus the git-install pip
refs in `CONTRIBUTING.md`/`docs/getting-started/installation.md`/
`ci/plugin-consumer.yml`. The GitHub repo starts from a single squashed
"initial public release" commit rather than carrying over the full CERN
GitLab history (a deliberate choice — GitLab retains the full history).
The two secondary items below (real second-machine fresh-user test,
dependency-vulnerability audit) remain genuinely open — not resolved by
this update.

## Verdict: **NO-GO for public stable release. GO-adjacent — one real decision away.**

This is not "lots more cleaning needed." Every P0 blocker found this
session has been fixed and verified. What remains blocking is almost
entirely decisions only you can make, not engineering work.

## Checklist against plan §15's criteria

| Criterion | Status |
|---|---|
| All P0 blockers closed | ✅ Both found P0s fixed: the wheel-breaking packages-list gap, and (separately) a lazy-import bug caught mid-rename by the test suite. |
| All P1 items closed or formally reclassified | ✅ Both P1s closed: the internal-citation cleanup, and `ci/stale_reference_check.sh`'s false positives (fixed this conversation). |
| Release acceptance passes | ✅ Full suite: 1286 passed, 12 skipped, 0 failed. `mkdocs build --strict` clean. All CI lint scripts (`agnosticism_check`, `import_direction_check`, `stale_reference_check`, `vision_pipeline_demo_reference_check`) pass. |
| Package install passes | ✅ Real clean-venv wheel installs verified repeatedly this session, most recently after the full package-naming rework — `forge --version`/`doctor`/`init` all work, `forge init`'s complete scaffold→build→test→report chain passes from the installed wheel. |
| Docs pass | ✅ Strict build, link crawl, offline-asset check, generated-page staleness, root-mirror sync all pass. |
| External-user test passes | ⚠️ **Partial.** A real clean-venv install + `ci/fresh_user_check.sh` (its own from-scratch venv) both passed — but both ran in this same sandbox. Plan §14 asks for a genuinely separate machine/account. Not done. |
| Licensing/security review passes | ⚠️ **Partial.** Secrets scan (targeted grep) clean, vendored-asset attribution confirmed, dependency surface is minimal (`pyyaml`, `jinja2` only). Full dependency-vulnerability audit (plan §12.1) not run. |
| Stable scope is documented | ✅ `docs/internal/release/release_scope.md`. |
| Release artifacts are final | N/A — no RC has been cut yet (by design; not attempted this session). |
| **Public hosting targets are ready** | ✅ **Resolved.** `https://github.com/pleguina/forge` is the canonical public host; every `<org>` placeholder is updated. |

## What's actually blocking

One real thing: **where does this get published, and under what org
name.** `CONTRIBUTING.md` already resolved the *how* (git-install, not
PyPI — a permanent decision, not a placeholder, due to the `forge` name
already existing on PyPI) and the CERN GitLab origin remains
authoritative until the new host is picked. This is not something to
guess at or clean your way past — it needs you to pick a destination.

Two secondary items, lower stakes:

- A real second-machine/second-account fresh-user test (plan §14) — the
  in-sandbox proxy is solid evidence but isn't the same claim.
- A real dependency-vulnerability audit (plan §12.1) — low risk given
  the minimal dependency surface, but not yet actually run.

## What would make this a clean GO

1. You pick the public host/org. Update the 4 files above, cut the
   `vX.Y.Z` tag from this commit (or whatever commit is current then).
2. Either accept the in-sandbox external-user evidence as sufficient, or
   run it once for real on a separate machine.
3. Run a real dependency audit (`pip-audit` or equivalent) — quick, and
   closes the last open plan §12 item.

Nothing else in this repo is blocking a release right now.
