# Migration tooling

Release-plan §2.8: migration commands (or an equivalent supported
workflow) for partition strings → structured coordinates, schema-version
insertion, old project layout, deprecated command/configuration names,
and compatibility-mode contract inference — each supporting dry-run and a
readable diff.

All five are implemented as one CLI command, `forge topgen migrate --kind <kind>`,
backed by pure functions in `forge/generation/migrate.py`. Every kind:

- computes its change fully in memory first;
- always prints a readable diff (`difflib.unified_diff` for text-content
  kinds) or a plain list of planned actions (filesystem-move kinds) —
  in both `--dry-run` and real-run mode, so `--dry-run` is a strict subset
  of real-run behavior (same output, minus the actual write);
- only writes to disk when `--dry-run` is **not** passed;
- exits `0` on success (including "nothing to migrate"), `2` on a hard
  error.

**Why text-level edits, not YAML round-tripping**: only plain PyYAML is
used in this project (no `ruamel.yaml`). A full `yaml.safe_load` → mutate
→ `yaml.dump` round-trip on a hand-authored, comment-heavy file would
silently strip every comment and reflow formatting — unacceptable for
files developers maintain by hand. Every kind below does a targeted
insert/replace of specific lines instead, leaving the rest of the file
byte-for-byte unchanged.

## `--kind schema-version`

```bash
forge topgen migrate --kind schema-version --file design.yml [--schema-kind design|registry|interface|verify_contract] [--dry-run]
```

Inserts the current supported schema version (release-plan §2.7 —
`docs/development/SCHEMA_VERSIONING.md`) into a file that doesn't declare
one yet. Auto-detects which of the four schemas `--file` is from its
filename (`design.yml`, `modules.yml`, `*.interface.yaml`,
`design.verification.yml`/`verify.design.yml`); `--schema-kind` overrides
this when the filename doesn't match the convention. A no-op ("already
declared") is not an error.

## `--kind partition-to-coordinates`

```bash
forge topgen migrate --kind partition-to-coordinates --contract m.interface.yaml --axis label [--role ROLE] [--dry-run]
```

Wraps a role's `partition: X` as a single-axis `coordinates: {<axis>: X}`.
**This is a 1-axis wrap only** — there is no safe, general way to
decompose an arbitrary partition string (e.g. `"sector2_station1"`) into
multiple named axes without a user-supplied mapping, so genuine
multi-axis decomposition is not attempted; do that by hand if you need
more than one axis. `partition:` is not deprecated (see
`docs/IP_INTERFACE_POLICY.md` "Partitioned roles") — this migration is
opt-in convenience, never a required step. Without `--role`, every
eligible role (has `partition`, no `coordinates` yet) in the contract is
migrated in one pass. With `--role`, only that role is touched; the
command refuses (exit 2) rather than guessing if the named role doesn't
exist or already has `coordinates`.

## `--kind legacy-plugin-layout`

```bash
forge topgen migrate --kind legacy-plugin-layout --plugin-root plugins/my_plugin [--dry-run]
```

Fixes the one documented "old project layout" case (`MIGRATION.md`):
plugins scaffolded before a `forge/verification/__main__.py` fix had
`<plugin>/verify/` instead of `<plugin>/forge/verify/`, with a dead
`_FW_PYTHON` `sys.path` hack in generated `bootstrap.py`/
`gen_stimulus.py`. This command moves the directory and, only when it
finds a confident, contiguous block of `_FW_PYTHON`-marker/`sys.path`
lines, removes it. If the marker is present but the pattern isn't
recognized cleanly (not contiguous), it's reported as needing manual
removal rather than guessed at destructively — this command never deletes
code it isn't sure about.

No live plugin in this repository still has the old layout (it's fully
historical), so this is exercised by synthetic fixtures in
`forge/tests/test_migrate.py`/`test_topgen_migrate_cli.py`, not a real
plugin — the logic is real and tested, it just has no remaining target in
this repo to apply it to.

## `--kind rename-verify-contract`

```bash
forge topgen migrate --kind rename-verify-contract --plugin-root plugins/my_plugin [--dry-run]
```

Renames the deprecated `verify.design.yml` filename to the current
`design.verification.yml` (same schema — `forge/verification/design_contract.py`
accepts either as a fallback, but `design.verification.yml` is canonical).
A pure filesystem rename, so there's no comment-loss risk here at all.

## `--kind infer-contract`

```bash
forge topgen migrate --kind infer-contract --ip-info ip_info.yaml --module NAME --output NAME.interface.yaml [--dry-run]
```

Generates a conservative `*.interface.yaml` skeleton for a module
currently running in compatibility mode (no interface contract — see
`MatchReport.compat_mode_modules`, `forge/contracts/matcher.py`). Only
`clock_primary`/`reset_primary` are inferred, using the same conservative
name heuristics (`ap_clk`/`clk`/`clock`, `ap_rst`/`rst`/`reset`/`rst_n`)
`auto_match_ports` already uses for compat-mode wiring. **Every other
port is listed as an explicit TODO comment, never assigned a role** — a
data port's semantics (wiring_kind, direction beyond what ip_info already
states, coordinates, protocol) cannot be safely guessed from its name
alone, and a migration tool that fabricated them would be worse than no
contract at all. Refuses to overwrite an existing file at `--output`.

## Not automated (and why)

- **The ARC → FORGE rename** (`MIGRATION.md`) is fully historical and
  closed — there's no live old-layout path left in this repository to
  migrate away from, so there is nothing to script.
- **`--use-kind-subdir`** (`forge/verification/__main__.py`) already prints its
  own runtime deprecation warning at the point of use; it's a behavior
  flag, not a file-schema migration concern.
- **`bx_counter`** (silently dropped, `forge/contracts/config.py`) has no
  dedicated migration command — it's already a complete no-op wherever it
  appears, so there's nothing broken to fix; a future major version may
  add a cleanup pass if stray keys become a documentation/clarity concern.
