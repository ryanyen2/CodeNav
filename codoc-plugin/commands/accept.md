---
allowed-tools: Bash
description: Accept a codoc proposal by slug or HLC prefix. Usage: /codoc-accept <slug-or-hlc>
---

Accept the specified codoc proposal:

<bash>
codoc accept "$1" --root-dir "$CLAUDE_PROJECT_DIR"
</bash>
