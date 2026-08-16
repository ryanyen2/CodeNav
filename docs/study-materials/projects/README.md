# The study codebases

Authored here as real source, not as tarballs. hearth and ember exist only as
archives, which means nobody has ever reviewed a line of them.

| | scribe | tally |
| --- | --- | --- |
| One sentence | Text pulled out of a PDF, into clean Markdown | A bank export, into a monthly summary |
| Files | 9 | 9 |
| Lines of code, excluding blanks and prose | 277 | 223 |
| Policies | 9 | 9 |
| Tests | 54 | 43 |
| Sample inputs | 3 | 3 |
| The coupled pair | Furniture is stripped before headings are found | Transfers are found before duplicates are dropped |

Each project holds four documents:

- `README.md` — what a developer opening the folder reads
- `ABOUT.md` — what the participant reads, two minutes, no assumed knowledge
- `STUDY.md` — the task card, the four rated decisions, the twelve-question quiz
- the code, the fixtures and the tests

`STUDY.md` is never shipped to a participant.

## Why these, and not hearth and ember

The first pilot could not run. Two thousand lines across fifteen files, a few
minutes, a stranger: both conditions floored and nothing was compared. The
bottleneck was search, and an agent searches instantly, so the human was a
spectator and the two arms looked alike.

These are built so the task is **easy to implement and hard to decide**. The agent
writes the change in a minute either way. What the person has to supply is
judgement, and judgement depends on knowing why the existing code decided what it
decided — which is the thing under test.

The design is `../../plans/2026-08-16-001-task-redesign.md`.
