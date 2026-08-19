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

- `README.md`: what a developer opening the folder reads
- `ABOUT.md`: what the participant reads, two minutes, no assumed knowledge
- `STUDY.md`: the task card, the planted problems and their rating guides, the follow-up request, and both five-question sets. A project plants as many as its recorded session landed, which is three for scribe
- the code, the fixtures and the tests

`STUDY.md` is never shipped to a participant.

## Why these, and not hearth and ember

The first pilot could not run. The original codebases had two thousand lines
across fifteen files. A stranger working in a few minutes scored at the floor in
both conditions, so there was nothing to compare. The bottleneck was finding
code, and an agent finds code instantly, so the participant had nothing to do and
the two conditions looked alike.

These projects are built so the task is **easy to implement and hard to decide**.
The agent writes the change in a minute in either condition. What the participant
has to supply is judgement, and judgement depends on knowing why the existing code
made the choices it made. Understanding those choices is the thing the study
measures.

The design is `../../plans/2026-08-16-001-task-redesign.md`.
