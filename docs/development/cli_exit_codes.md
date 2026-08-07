# forge CLI: exit codes and the output envelope

This document is the single source of truth for what a `forge` (and
`forge verify`) command's exit code means. It is cross-referenced from each
migrated command's `--help` epilog rather than duplicated there.

## Historical inconsistency (resolved)

Exit code `2` used to mean three different things depending on which file
raised it:

- `core/cli/main.py`: an unexpected, uncaught internal exception.
- `core/cli/groups/core.py`, `core/cli/groups/topgen.py` (`init-plugin`):
  a CLI usage error (bad arguments, missing required file).
- `verify/release_check.py`'s own docstring: a hard contract-verification
  failure.

Every `--json`-supporting command also invented its own JSON shape (see
`forge/core/cli/envelope.py`'s module docstring for specifics). Both
inconsistencies are now closed: every command below shares one
`CommandEnvelope` and one exit-code policy.

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
  merged into one registry (a full merge is out of scope: the two
  families serve different subsystems and merging them would break their
  existing stable codes for no real benefit).
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

## Command coverage

All commands below emit `CommandEnvelope` under `--json` (schema version
`0.1.0`) and follow the exit-code policy above.

| Command | Notes |
|---|---|
| `forge doctor` | `--strict` genuinely fails on any missing check (previously dead code — no check was ever `required=True`). |
| `forge verify doctor` | |
| `forge verify release-check` | |
| `forge inspect` | Plain `--json` is a summary (counts + maturity + diagnostics), not a full IR dump — use `--emit-ir` for the full canonical IR. `--diff`/`--explain-staleness` results live in `metrics`. |
| `forge build` | `{"plan","plan_hash"}` full-dump replaced with `metrics.counts` + `artifacts` + `diagnostics`. Supports `--strict`/`--dry-run`/`--provenance`/`--explain-staleness`. |
| `forge test check-only` / `prepare` / `run` | `run`'s `metrics` carries `events_run`/`events_passed`/`events_failed`; supports `--junit-xml`. |
| `forge report` | `artifacts` lists every file written; sections degrade to an honest "not available" note (`diagnostics` severity `note`) when optional inputs (`--hls-build-root`, `--probe-csv`, `--provenance`, `--junit-xml`) aren't given. |
| `forge init` | `metrics.steps_completed` only ever lists steps that actually succeeded — a failed step halts immediately. |
| `forge topgen validate` | Bespoke `{"passed","registry","design","stale"}` replaced; `metrics.stale` keeps the same `{"count","artifacts"}` shape as before for `--check-stale`. |
| `forge topgen validate-registry` | Bespoke `{"passed","errors","warnings","infos"}` replaced with `diagnostics`. |
| `forge core resources` | `--format json` (pre-existing) is a deprecated alias for the new `--json`; both produce the envelope, with the original `{key: {path, exists}}` dict now at `metrics.resources`. |
| `forge core verify-contract` | Exit-code conflation fixed (see above). |
| `forge analyze hls-report` / `latency-check` / `runtime-latency` / `plot-results` / `dashboard` | Lighter-touch pass (report-generation commands, not pass/fail gates — `metrics`/`artifacts` matter more than `diagnostics` here). Non-JSON text output and exit codes are unchanged. |

**Not on the envelope (deliberate)**: `forge verify
preflight` (a real subset of `forge verify doctor`, kept separate and
narrower/faster for tight verify-iteration loops), `forge topgen
gen-top`/`hls *`/`framework *` (detailed subsystem commands the golden
path wraps rather than replaces), and `forge topgen
migrate` (a one-off developer tool, not a pass/fail gate or report
generator).
