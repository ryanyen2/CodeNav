# codoc for Claude Code

What `codoc init` (or `codoc install-hooks`) installs into a repo so Claude Code
works with its feature tree.

| part | file | what it does |
|---|---|---|
| hooks | `hooks/hooks.json` | tell the codoc daemon when an agent session starts and ends and which files it touched, so the daemon reflects the session's code changes once, when it closes |
| skill | `skills/codoc-intent/SKILL.md` | how to read and update the tree through the MCP tools |
| commands | `commands/codoc/{sync,plan,ask}.md` | `/codoc:sync` builds queued edits, `/codoc:plan` proposes a change as tree edits, `/codoc:ask` answers a question as a reading path over the tree |
| MCP server | registered at Claude Code's local scope | the `codoc_*` tools |

The hooks and the `mcp__codoc__*` permission go in the untracked
`.claude/settings.local.json`; the skill and commands go in `.claude/`.

Install:

```bash
codoc init            # in the repo; installs everything
codoc install-hooks   # once per clone, or to upgrade the Claude Code parts
```
