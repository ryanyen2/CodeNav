"""The Claude Code integration.

- ``hook``          — the handler Claude Code's hooks run: opens and closes the
                      agent epoch and tells the session when work is queued.
- ``install_hooks`` — writes the hooks, the MCP registration, the skill and the
                      ``/codoc:*`` commands into a repository.
- ``autorealize``   — start an unattended realize pass (``codoc watch --auto-realize``).
- ``sdk_realize``   — run the realize queue through the Claude Agent SDK.
- ``plugin/``       — the files ``install_hooks`` installs.
"""
