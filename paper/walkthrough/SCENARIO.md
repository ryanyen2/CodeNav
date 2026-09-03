# Example usage scenario for CoDoc

Nadia is a developer who maintains a flocking simulation that runs on a display in a
museum lobby. They have recently installed CoDoc as an extension in their editor. The
simulation is about 500 lines of TypeScript across 13 files, and it draws a flock of
birds that steer by three local rules, a hawk that hunts them, a wind, and a cycle
from day to night. A coding agent has been changing the code alongside Nadia for
several weeks, and Nadia has not read most of what it wrote.

**First Steps:** Nadia opens the repository. CoDoc opens a feature document beside the
source. It lists eighteen features in five groups ①, named for what the simulation
does rather than for the files the code sits in. Under each title CoDoc lists the code
that feature owns ②, and under that is a paragraph saying what the feature is
responsible for ③. "Edge behaviour" owns code in `world.ts` and in
`render/trails.ts`; neither file is named for the rule, and neither holds all of it.
Nadia can read the document three ways ④: as prose, as the outline alone, or marked
with who wrote each part.

**From a Question to a Path:** Visitors have told Nadia that the flock jumps across
the display, and Nadia wants to know whether that is a fault or a decision. Rather
than open the physics code, Nadia asks the document.

    /codoc:ask what happens when a boid reaches the edge of the world, and why
    does its trail break there?

The agent reads the outline and the code, and answers by numbering four features that
already exist. A bar carries the question and a stepper through the stops ⑤, each stop
sits on a feature ⑥, and on each one the clause that carries part of the answer is
picked out ⑦. The same numbers appear in the outline ⑧, and the stops are grouped by
the stage of the answer they belong to ⑨. The world wraps: a boid that leaves one edge
re-enters from the opposite one, and its trail is broken at the boundary so that no
false line is drawn back across the sky. The jump is a decision. Nothing has been
written to the document, and the toolbar still reads "in sync".

**From a Sentence to a Draft:** On a display this size a teleport reads as a fault, so
Nadia decides the flock should turn instead. Nadia rewrites the description of "Edge
behaviour" to say what the world should do, and marks the phrase that matters most in
bold. The rewritten words are inked while the code does not say them yet ⑩, a marker
appears on the feature ⑪, and the bolded phrase is the part the agent will be told to
get right first ⑫. No code has been written, because an edit to the document counts as
documentation until Nadia hands it off. Had Nadia only been correcting a stale
sentence, the edit would have ended here.

**From a Draft to a Diff:** Nadia hands the draft off. CoDoc assembles one request out
of the new description, the bolded phrase, and the code that "Edge behaviour" owns,
and puts it in front of the coding session Nadia already has open. The agent changes
that code, and on the display the flock banks away from the walls instead of crossing
the frame. In the document the ink clears ⑬ and the marker fills ⑭, which is CoDoc
saying that the codebase now claims what the sentence claims.

**When the Agent Has to Decide:** The hawk's dive reads weakly on the display, so
Nadia turns to "Panic". Nadia selects the sentence that says what a frightened bird
does and comments on it: "Panicked boids should break formation and dive." The request
is underspecified on purpose, because Nadia has not said which of the three flocking
rules a frightened bird drops, or which way a dive points. The agent decides both,
changes three files, and writes what it decided back into the description. The feature
is marked because what was built differs from what was asked for ⑮, Nadia can keep the
new wording or restore their own ⑯, wording the build added sits on a green ground and
wording it dropped is struck where it stood ⑰, and the comment that caused it carries
the agent's reply ⑱. Reading it, Nadia sees that the agent threw away all three
flocking rules during a panic and read "dive" as straight down, and rewrites those two
clauses in place.

**From Code back to the Document:** A teammate pushes a change that adds a fixed
pillar to the world and a force that steers birds around it. Nothing in the document
asked for it. CoDoc reads the repository, finds code that no feature owns, and
proposes a feature for it, drawn at the position it would occupy if accepted ⑲, with
its description on the ground CoDoc uses for what the code did ⑳. Nadia accepts.

**From a Task to a Plan:** Nadia wants the flock to settle at night rather than fly at
full speed until morning, and does not yet know how it should be organised. Nadia asks
for a plan instead of a change.

    /codoc:plan at night the flock should settle and roost instead of flying at
    full speed until morning

The agent answers with proposals in the document and writes no code. Nadia can accept
the whole plan or take the nodes one at a time ㉑, what accepting means is said on the
node rather than in a dialog ㉒, and the planned wording is drawn in the gray CoDoc
uses for what nobody has built ㉓. The agent proposes a new feature called "Roosting",
and amends two features the change would make untrue, one of which Nadia never
mentioned. Nadia accepts the plan, and only then does the agent write code.

---

## Figures

| # | Shows | Steps |
| --- | --- | --- |
| 1 | The feature document | ① ② ③ ④ |
| 2 | A question answered as a path | ⑤ ⑥ ⑦ ⑧ ⑨ |
| 3 | An edit held as a draft | ⑩ ⑪ ⑫ |
| 4 | The same sentence, once the code says it | ⑬ ⑭ |
| 5 | A comment, and what the agent decided | ⑮ ⑯ ⑰ ⑱ |
| 6 | A feature proposed from code | ⑲ ⑳ |
| 7 | A plan, before any code | ㉑ ㉒ ㉓ |

Every figure is a screenshot of the CoDoc extension running against the `boids-demo`
repository, taken on 2026-09-03, with a numbered annotation layer drawn over it in one
colour the product does not use. Nothing about the interface is redrawn.

The states were produced by driving the workspace from the shell and letting the
extension render what it found, which is why they are real rather than staged. The
question in step 2 is a live `/codoc:ask` run; the draft is a description edit applied
through the daemon; the diff in step 5 is the recorded agent take from `.demo/woz/`;
the proposal in step 6 is a live Loop A pass over `.demo/surprise/`; and the plan in
step 7 is a live `/codoc:plan` run. `PROVENANCE.md` says which command produced each
one.
