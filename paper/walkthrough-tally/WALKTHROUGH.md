# Example usage scenario for CoDoc

Paper text and shot list for the walkthrough. Every figure is a screenshot of a real
session with an annotation layer on top. Nothing about the interface is redrawn. The
session is `video/codoc-record-trimmed.mp4`, 2 minutes 41 seconds, recorded
2026-08-20 on the `tally` workspace.

A note on subjects, kept consistent throughout. "We" means a design decision the
authors made. "CoDoc" means what the running system does. "The developer" and "the
agent" are the two parties who write the document.

---

## The program

`tally` reads bank statement CSV files and prints a spending summary. It is eleven
Python files, and no single one of them is "how a payment gets a category". The
reading is in `rows.py`, the sign convention in `money.py`, the matching in
`categories.py`, the totals in `summary.py`, and the rules those parts have to obey
in `tests/`. Reading the files tells you what the program computes. It does not tell
you what the program is for.

**Figure 1.** CoDoc opens a feature document beside the source. Twenty six features,
grouped by what the program does with a statement: reading it, applying policies to
it, producing a summary, and checking the result. Each feature is a title, the code
it owns, and a paragraph saying what that part is responsible for. The link is
visible from the code side too, above the function it covers.

---

## Step 1. Asking the document a question

The developer wants to know how a payment ends up in a category, and asks in the
document rather than in a chat window.

> `/codoc:ask how the payment is put into the category`

**Figure 2.** The agent reads the tree and the code, then draws a numbered path over
two features that already exist. Each stop highlights the clause in that feature's
own prose that answers the question, and hangs one line beside it saying what the
stop contributes. The same numbers appear in the outline, so the path is navigable.

We made the answer a path through the document rather than a paragraph in a chat
pane, because a reader who finishes a chat answer still has to find the place in the
system it was about. The overlay writes nothing to the document, which is why the
toolbar still reads "in sync" while it is on screen.

---

## Step 2. Asking for a change

The developer wants two things: the merchant rules in a file they can edit, and a
summary at a finer grain than a whole month.

> `/codoc:plan i want to manage the categories myself without touching the source,
> and i want to see spending at a finer grain than a whole month`

**Figure 3.** A request can also be attached to the sentence it is about. Selecting
a sentence offers one action, which is to say what it should do instead, and the
code listed under that feature's title becomes the scope of the request. Both routes
end in the same place, which is a request that names a feature rather than a file.

---

## Step 3. The agent works where the developer can see it

**Figure 4.** While the agent reads the codebase, the document says which feature it
is inside and which file it is reading. The outline carries the same marks, and the
file tree shows what it has touched. In the terminal it states the shape it has in
mind before writing any of it.

We put the activity on the features rather than in a progress log because the
question a reader has during a long run is which part of the system is being
changed, and a list of file paths does not answer it.

---

## Step 4. The plan arrives in the document, before any code

**Figure 5.** The agent answers with proposals in the document. A planned feature is
drawn where it would sit, in the gray CoDoc uses for wording nobody has built, with
its verdict on the node itself rather than in a dialog. The row under the title says
what accepting means: the agent writes this, and the code will change. The developer
accepts the whole plan, or takes the nodes one at a time.

Reviewing at the level of intent is cheaper than reviewing a diff, and it happens
before the diff exists. The developer is reading four sentences about what the
program will do, not four hundred lines about how.

---

## Step 5. The code comes back into the document

**Figure 6.** The agent implements the accepted nodes, runs the tests, and stops.
CoDoc then reads the repository and finds descriptions the code has outgrown. It
rewrites three of them, struck where the old sentence stood and replaced in place, so
the developer can compare the claim with what the code now does. Keep them, or
restore your own wording on any one of them.

One change is still a proposal rather than a rewrite, because it would alter the
shape of the document rather than the wording of a feature. A change to the shape is
always a proposal a person approves, because that judgment is approximate.

---

## What we built, and what it makes possible

We built four things, and each is a mechanism rather than a workflow. First, a
document whose unit is a feature rather than a file, with links into the code that
CoDoc keeps current on its own. Second, a request that names a feature, so its scope
is the code that feature owns. Third, a proposal drawn into the document at the
position it would take, so a change is reviewed where it will live. Fourth, a record
of who wrote which wording and what each change was answering.

What people can do with the four mechanisms is what the walkthrough shows. The
developer can ask a question and get a reading path through the document instead of
a paragraph to re-situate. They can ask for a change in one sentence and watch which
feature the agent is inside while it works. They can approve a design before a diff
exists. They can see which of their own sentences the code has outgrown, and decide
one at a time whether to keep the new wording.

| CoDoc does | The developer does | The agent does |
| --- | --- | --- |
| Keeps the record of which code implements which feature current across edits, renames, and moves | Writes what each feature is for, and decides what to ask for | Answers a question by drawing a path over features that exist |
| Draws every proposal in the position it would occupy | Accepts, rejects, or edits every proposal | Proposes a plan as document nodes before writing code |
| Rewrites a description the code has outgrown, and offers to restore the old one | Keeps the new wording or restores their own | Reports which feature it is inside while it works |

---

## Shot list

Timecodes are from `video/codoc-record-trimmed.mp4`. The ones marked with a check
were read off the frame; the rest are the ranges around them.

| Time | What happens | Figure |
| --- | --- | --- |
| 0:00 to 0:18 | Reading the document. Twenty six features, plain prose, nothing marked ✓ | 1 |
| 0:20 to 0:30 | Typing `/codoc:ask how the payment is put into the category` ✓ | |
| 0:30 | The agent reads the tree and the code ✓ | |
| 0:39 to 0:46 | The walkthrough is drawn: two numbered stops, notes in the margin ✓ | 2 |
| 1:03 | Typing the change request as `/codoc:plan …` ✓ | 3 |
| 1:06 to 1:12 | The agent reads the codebase; the document shows which feature it is in | 4 |
| 1:14 | Four proposals in the toolbar, with Accept all and Accept and build ✓ | 5 |
| 1:20 to 1:55 | The agent implements and runs the tests ✓ | |
| 1:58 to 2:01 | The build lands; CoDoc rewrites the descriptions the code outgrew ✓ | 6 |
| 2:04 to 2:41 | The tree is translated into Traditional Chinese ✓ | |

The translation at the end is a separate feature and is not part of the walkthrough
above. It is left in the shot list because it is in the recording.

---

## Provenance

- The feature tree, all 26 titles and descriptions, is `.codoc/tree.codoc` in the
  `tally` workspace as it stood at the end of the session.
- Figures 1, 3, 4, 5 and 6 are screenshots of the session, supplied at 2000 pixels
  wide. Figure 2 is frame 39.5 s of `codoc-record-trimmed.mp4`, cropped to the
  editor and the terminal.
- Every quoted command and every quoted line of agent output is read off the frame,
  not reconstructed.
- The annotation layer is the only thing added: numbered circles, leader lines, and
  outlines around the control being pointed at, in one colour that the product does
  not use.

## What the walkthrough does not show

The recording has no moment where a person edits a description and hands it off, so
the walkthrough describes requests as they were actually made, which was by prompt
and by comment. The blame and history surfaces are not in the recording either. If
either is wanted as a figure, it needs a new capture; do not draw it.
