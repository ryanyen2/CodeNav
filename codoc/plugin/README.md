# codoc-intent Claude Code Plugin

Provides the `codoc-intent` skill and hooks that wire Claude Code into the codoc
two-loop system.

## What it does

- **Hooks** (`hooks/hooks.json`): fires on `SessionStart`, `Stop`,
  `PreToolUse(Edit|Write|MultiEdit|Read)`, and `PostToolUse(Edit|Write|MultiEdit)`
  to maintain `.codoc/activity.json` — the live agent-epoch + touch-log used by
  the watch daemon for loop-safe reconciliation and by the VS Code extension for
  live gutter decorations.
- **Skill** (`skills/codoc-intent/SKILL.md`): teaches Claude Code the
  *propose-then-implement* workflow — expressing code changes as codoc proposals
  first (via `codoc propose`), letting the user Accept in the IDE, then having
  Loop B drive implementation.

## Installation

Run `codoc init` in your repo — it installs the hooks and skill automatically
into `.claude/settings.json` and `.claude/skills/`.

Or manually:
```bash
codoc install-hooks --root .
```

## Manual plugin install (optional)

If you prefer the CC plugin mechanism:
```bash
claude --plugin-dir /path/to/codoc/plugin
```
