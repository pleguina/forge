# forge CLI: exit codes and the output envelope

This document is the single source of truth for what a `forge` (and
`forge verify`) command's exit code means. It is cross-referenced from each
migrated command's `--help` epilog rather than duplicated there.

## Before Phase 6

Before the release-plan's Phase 6 (`§6.7`), exit code `2` meant three
different things depending on which file raised it:

- `core/cli/main.py`: an unexpected, uncaught internal exception.
- `core/cli/groups/core.py`, `core/cli/groups/topgen.py` (`init-plugin`):
  a CLI usage error (bad arguments, missing required file).
- `verify/release_check.py`'s own docstring: a hard contract-verification
  failure.

Every `--json`-supporting command also invented its own JSON shape (see
`forge/core/cli/envelope.py`'s module docstring for specifics). Phase 6
closes both inconsistencies, one command at a time (see
`docs/development/release-readiness.md`'s Phase 6 section for which
commands have been migrated so far).

## The policy

Commands migrated onto `forge.core.cli.envelope.CommandEnvelope` follow one
policy:

| Exit code | Meaning | `CommandEnvelope.status` |
|---|---|---|
| `0` | Command completed; no blocking issues found. | `pass`, or `warn` without `--strict` |
| `1` | Command completed but found a blocking issue (or a warning, under `--strict`). | `fail`, or `warn` with `--strict` |
| `2` | The command itself could not run to completion — a usage error or an unexpected internal exception. This is the *only* remaining meaning for `2`. | `error` |

`--strict` promotes `warn` to the same exit code as `fail` (`1`) without
changing the reported `status` string — the JSON output still says `"warn"`,
only the process exit code changes. This lets CI scripts fail a build on
warnings without losing the distinction between "found a real problem" and
"found something worth a human's attention."

## The envelope shape

```json
{
  "schema_version": "0.1.0",
  "status": "pass | warn | fail | error",
  "diagnostics": [
    {"code": "FWV004", "severity": "error", "message": "...", "action": "..."}
  ],
  "artifacts": ["path/to/file/this/command/wrote"],
  "metrics": {"command-specific": "structured data"},
  "next_actions": ["de-duplicated suggested next steps, in order"]
}
```

- `diagnostics` reuses the same per-item shape `Diagnostic.to_dict()`
  (`forge/verify/diagnostics.py`) and `ATGDiagnostic.to_dict()`
  (`forge/core/diagnostics.py`) already produce — those two stable code
  families (`FWVxxxx`, `ATGxxxx`) are **bridged** onto the envelope, not
  merged into one registry (the release plan's own text only asks for a
  bridge; see `docs/development/release-readiness.md`'s Phase 6 closure
  notes for why a full merge is out of scope).
- `metrics` is where query results and summary counts live —
  `--diff`/`--explain-staleness`-style output, plan summaries, per-event
  test counts, and so on. It is not diagnostics and not artifacts.
- The human-readable text form (`forge <cmd>` without `--json`) is always
  rendered *from* the `CommandEnvelope` object
  (`forge.core.cli.envelope.render_human`) — never assembled independently
  — so the two output modes cannot silently drift apart.

## Reconciled exit codes (fixed during the sweep)

Two real conflations of exit code `2` (found while migrating each command
onto the shared envelope, not merely renamed) were fixed as part of this
sweep — both are deliberate, documented behavior changes:

- `forge core verify-contract`: a real contract-verification failure
  (`VerifyResult.errors`) used to exit `2` (via `VerifyResult.exit_code()`'s
  own errors→2 convention) — now exits `1` (`status: "fail"`). `2` is used
  there only for genuine usage errors (missing `--ip-info`/`--contract`
  file, neither `--contract` nor `--all-contracts` given).
- `forge core resources --key <unknown>`: used to exit `1` — now exits `2`
  (a bad `--key` value is a usage error, not a "the resource doesn't
  exist" finding).

## Migration status

All commands below emit `CommandEnvelope` under `--json` (schema version
`0.1.0`) and follow the exit-code policy above.

| Command | Notes |
|---|---|
| `forge doctor` | §6.3. `--strict` now genuinely fails on any missing check (previously dead code — no check was ever `required=True`). |
| `forge verify doctor` | §6.0. First migration (the proof). |
| `forge verify release-check` | §6.0/§6.3. |
| `forge inspect` | §6.1. Plain `--json` is now a summary (counts + maturity + diagnostics), not a full IR dump — use `--emit-ir` for the full canonical IR. `--diff`/`--explain-staleness` results moved into `metrics`. |
| `forge build` | §6.2. `{"plan","plan_hash"}` full-dump replaced with `metrics.counts` + `artifacts` + `diagnostics`. New `--strict`/`--dry-run`/`--provenance`/`--explain-staleness`. |
| `forge test check-only` / `prepare` / `run` | §6.4 (new command). `run`'s `metrics` carries `events_run`/`events_passed`/`events_failed`; supports `--junit-xml`. |
| `forge report` | §6.5 (new command). `artifacts` lists every file written; sections degrade to an honest "not available" note (`diagnostics` severity `note`) when optional inputs (`--hls-build-root`, `--probe-csv`, `--provenance`, `--junit-xml`) aren't given. |
| `forge init` | §6.6 (new command). `metrics.steps_completed` only ever lists steps that actually succeeded — a failed step halts immediately. |
| `forge topgen validate` | §6.7. Bespoke `{"passed","registry","design","stale"}` replaced; `metrics.stale` keeps the same `{"count","artifacts"}` shape as before for `--check-stale`. |
| `forge topgen validate-registry` | §6.7. Bespoke `{"passed","errors","warnings","infos"}` replaced with `diagnostics`. |
| `forge core resources` | §6.7. `--format json` (pre-existing) is a deprecated alias for the new `--json`; both produce the envelope, with the original `{key: {path, exists}}` dict now at `metrics.resources`. |
| `forge core verify-contract` | §6.7. New `--json`. Exit-code conflation fixed (see above). |
| `forge analyze hls-report` / `latency-check` / `runtime-latency` / `plot-results` / `dashboard` | §6.7, lighter-touch pass (report-generation commands, not pass/fail gates — `metrics`/`artifacts` matter more than `diagnostics` here). New `--json` on all 5; non-JSON text output and exit codes are unchanged. |

**Not migrated (deliberately out of scope for Phase 6)**: `forge verify
preflight` (a real subset of `forge verify doctor`, kept separate per
rule 5/§6.6 — see `docs/development/release-readiness.md`'s Phase 6 slice
3 closure for the full containment table), `forge topgen gen-top`/`hls
*`/`framework *` (detailed subsystem commands the release plan's golden
path wraps rather than replaces — §6.1's own framing), and `forge topgen
migrate` (a one-off developer tool, not a pass/fail gate or report
generator).

See `docs/development/release-readiness.md`'s Phase 6 section for the
full evidence trail (investigation findings, real end-to-end test runs,
exact before/after test counts) behind every row above.
