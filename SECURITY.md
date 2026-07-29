# Security Policy

FORGE is a CLI/tooling project (topology generation, HLS orchestration,
and verification runtime) used in trigger/DAQ firmware development. It is
not exposed as a public service — most "security" concerns here are about
trusting inputs (design files, RTL, HLS output) from collaborators, not
network-facing attack surface.

## Reporting an issue

If you find a security-relevant bug (e.g. unsafe deserialization, command
injection via a design/config file, path traversal in generated artifact
writing), please report it privately rather than opening a public issue,
until a fix is available:

- On GitHub, use [private security advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
  (Security tab → "Report a vulnerability") if the repo has them enabled.
- Otherwise, contact a maintainer listed in `CODEOWNERS` directly.

Don't open a public issue with exploit details before a fix is available.

## Scope notes

- FORGE parses and generates files (YAML, JSON, Verilog/VHDL, SystemVerilog)
  based on plugin-authored config and RTL. It assumes plugin authors and
  their RTL/IP sources are trusted — it is not designed to sandbox
  untrusted input.
- Subprocess invocations (HLS tools, simulators, linters) use list-form
  arguments, not shell strings — if you find a code path using
  `shell=True` or string-interpolated shell commands, that's a bug, please
  report it.
