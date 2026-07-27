# Security Policy

FORGE is internal tooling (topology generation, HLS orchestration, and
verification runtime) used within trigger/DAQ firmware development. It is
not exposed as a public service — most "security" concerns here are about
trusting inputs (design files, RTL, HLS output) from collaborators, not
network-facing attack surface.

## Reporting an issue

If you find a security-relevant bug (e.g. unsafe deserialization, command
injection via a design/config file, path traversal in generated artifact
writing), please report it privately to the repository maintainers rather
than opening a public GitLab issue, until a fix is available. If you don't
have a private channel to a maintainer, open a GitLab issue marked
confidential on this project.

## Scope notes

- FORGE parses and generates files (YAML, JSON, Verilog/VHDL, SystemVerilog)
  based on plugin-authored config and RTL. It assumes plugin authors and
  their RTL/IP sources are trusted — it is not designed to sandbox
  untrusted input.
- Subprocess invocations (HLS tools, simulators, linters) use list-form
  arguments, not shell strings — if you find a code path using
  `shell=True` or string-interpolated shell commands, that's a bug, please
  report it.
