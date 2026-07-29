## What and why

<!-- What changed, and why — the diff already shows what, focus on why. -->

## Checklist

- [ ] `CHANGELOG.md` updated under `[Unreleased]` for any user-visible
      change (new command, changed default, bug fix, breaking change)
- [ ] `MIGRATION.md` updated if this breaks an existing consumer
      (renamed command, moved path, changed default behavior)
- [ ] Docs updated in the same change if behavior/paths changed — see
      `CONTRIBUTING.md`: "this repo has previously gone long stretches
      with docs describing a layout that no longer existed"
- [ ] Tests added/updated (see `CONTRIBUTING.md` for the local check
      commands — these are exactly what CI runs)
- [ ] `forge/` core changes don't hardcode detector/algorithm-specific
      assumptions (`ci/agnosticism_check.sh`)

## Test plan

<!-- How you verified this — commands run, output observed. -->
