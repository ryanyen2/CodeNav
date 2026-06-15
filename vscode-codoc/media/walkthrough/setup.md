## Set up codoc

One click provisions everything codoc needs — no terminal, no manual `pip`, no API key in the common case.

When you run **Set up codoc**, the extension will:

1. Install an isolated, version-pinned Python environment (via `uv`) — no system Python required.
2. Index your repo and propose an initial **feature tree** (`codoc init`).
3. Reuse your existing **Claude Code** login for codoc's reflection (or fall back to an OpenAI key).
4. Start the managed `codoc watch` daemon so the tree stays in sync as you edit — you never start or stop it.

> Setup requires a **trusted workspace** (it installs and runs the Python core). Tree navigation works untrusted; provisioning and the daemon do not.

[Set up codoc](command:codoc.setup)
