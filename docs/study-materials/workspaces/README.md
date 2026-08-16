# The four project copies

Built from the source in `../projects/` by
`../scripts/build-workspaces.sh`. Do not edit an archive: edit the project and
build again, or the change is in a tarball nobody can review.

| Archive | Unpacks to | What it is |
| --- | --- | --- |
| `scribe.tar.gz` | `scribe/` | scribe, with a codoc feature tree |
| `scribe-baseline.tar.gz` | `scribe-baseline/` | The same scribe, with a `CLAUDE.md` instead |
| `tally.tar.gz` | `tally/` | tally, with a codoc feature tree |
| `tally-baseline.tar.gz` | `tally-baseline/` | The same tally, with a `CLAUDE.md` instead |

**scribe** turns text pulled out of a PDF into clean Markdown. 277 lines of code
across 9 files, 54 tests. **tally** turns a bank export into a monthly summary.
223 lines across 9 files, 43 tests. Each participant uses one project each way.

## What is the same and what is different

Within a project the two copies have identical source, identical sample inputs,
identical tests and twelve identical commits. The only difference is where the
description lives: a codoc feature tree in one, `CLAUDE.md` in the other. Both
are written from the same source file in `../projects/<name>/CLAUDE.md`, so
neither arm can be told more than the other.

The commit counts match on purpose. `git log` is one of the ways a participant
can learn why something is the way it is, and an arm with a shorter history has
one less channel than the other.

## Checking a rebuild

```
tar tzf scribe.tar.gz | head
```

Then unpack both arms of one project and diff them. The only files that should
differ are `CLAUDE.md`, `.claude/skills/doc-maintenance/SKILL.md` and `.codoc/`.
