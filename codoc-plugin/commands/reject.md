---
allowed-tools: Bash
description: Reject a codoc proposal by slug or HLC prefix. Usage: /codoc-reject <slug-or-hlc>
---

Reject the specified codoc proposal:

<bash>
codoc reject "$1" --root-dir "$CLAUDE_PROJECT_DIR"
</bash>
