---
name: Bug report
about: Something in FORGE isn't working as documented
title: ""
labels: bug
---

**What happened**
A clear description of the incorrect behavior.

**Command and output**
```
$ forge ...
<paste the actual output/traceback here — re-run with --debug if it's a
generic "Unexpected error" so the traceback is included>
```

**Expected behavior**
What you expected to happen instead.

**Environment**
- `forge --version` output (or `git rev-parse HEAD` if running from source):
- `forge doctor` output (if relevant — toolchain/dependency issues):
- OS:

**Minimal reproduction**
If possible, the smallest `design.yml`/`modules.yml`/command that
reproduces it. `plugins/passthrough_demo/` is a good starting point to
strip down from.
