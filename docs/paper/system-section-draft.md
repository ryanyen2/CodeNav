# The System [draft v5, voice pass]

The intent behind a codebase is usually written down in the wrong place, if it is written down at all. It usually hides in a comment or a commit message, in a design doc that eventually outdated, or in a summary agent output at the end of each turn of the conversation; and in every one of those places it drifts away from the code when the code moves on.


CoDoc instead treating these intent. The design
rests on a few choices, about what that document is, how it stays true to the code, how a person
turns intent in it into code, and what happens when the document and the code disagree; we take
them one at a time and ground each in a single running task, an engineer named Alicia teaching a
small coding agent to survive an unreliable local model server. None of these are the only way
the system could have been built, and wherever we chose one way over a reasonable alternative we
say so, and say what it cost.

## The feature document

Everything in CoDoc hangs off one object, a document we call the feature document, which is a navigable outline of the codebase written in terms of higher-level feature or concept of the codebase rather than in terms of files. 
Its unit is a feature, a named piece of the system with a short description of what that piece is responsible for; and every feature is tied to the exact code that implements it through links we call bindings. 
One feature can bind many scraps of code scattered across many files, while any
one scrap of code answers to at most one feature, so the document reads as a clean carving of the
codebase into purposes rather than a second, tidier copy of the source. The point of having such
an object at all is to give the intent behind a codebase somewhere to live where a person can
find it, trust it, and edit it, which is something the running code, however clean, can never do
for itself.

So when Alicia opens the repository in Visual Studio Code, the feature document is sitting there
next to the code in a two-pane editor. She reads down the outline, lands on a feature titled
Ollama model backend client, and follows its binding straight to the class that implements it,
OllamaModelClient in mini_coding_agent.py, without having opened a single file first. CoDoc had
built that outline by parsing the repository into a syntax tree, gathering the pieces into
features, and nesting the features the way the code nests itself, so the outline carries the same
shape as the system and she moves through it the way she would move through the code.

We made the feature document something a person authors and reads, and we treat the code
attribution underneath it as a secondary index the system keeps fresh on its own. That one
decision is what separates CoDoc from the notes teams keep for their agents today. A CLAUDE.md
file or a memory bank is usually a summary the agent wrote about code that no one went back to
curate. In fact it tends to decay into a change log a reader cannot fully trust and cannot
cleanly edit. It also sets CoDoc apart from the recent tools that keep a live description pointed
at the machine rather than at the person. RPG-Encoder keeps a graph of what a repository can do
and holds it current as commits land, but that graph is built for an agent to traverse rather
than for a person to sit and read; and SpecLang keeps a specification written for the model
beside the code, and it drifts from the code the way ordinary documentation always has. We keep that
same live tie between description and code and hand it to a person to edit directly, which
follows Heer's argument that automation earns its keep when it is arranged around a representation
the person still holds. The cost of taking this position is real, the document is only ever as
trustworthy as the effort spent keeping it aligned; so the whole rest of the design is an attempt
to hold it true to the code while asking for as little of that effort as we can.

## Keeping the document true to the code

The first half of the work runs in the code-to-document direction, watching the code and quietly
updating the document to match; and its whole design goal is to swallow the constant churn of
ordinary coding by itself and to bother a person for judgment only where a judgment is genuinely
required. After Alicia's agent finishes editing mini_coding_agent.py, most of what changed is
dull from the document's point of view, an edited method here, a function that slid to another
file there, and CoDoc settles all of it just by comparing a fresh reading of the repository
against the bindings it already has. When a bound scrap of code is edited in place it refreshes
the binding; when the scrap is deleted it drops the binding; and when the scrap moves to another
file or gets renamed it follows the code and reattaches the same feature, so attribution usually
lives through ordinary refactoring on its own.

The cases this bookkeeping cannot settle on its own, however, are new code that no feature yet
describes, and a feature that has lost the last scrap of code it was bound to. The retry helper
the agent just wrote is the first kind, code nobody has claimed, so CoDoc spends a single model
call that sees the new code together with the whole outline and every feature title at once. The
uniqueness rule does one job here, it forces the model to pick a single owner for the helper
rather than smearing it across features; and seeing every existing title in that one pass does
the other, it nudges the model toward attaching the helper to the model client's feature instead
of spinning up a second feature that would only mean the same thing. Edits that touch only
wording or bindings CoDoc simply applies; anything that would change the shape of the outline,
say adding a feature, moving one, or retiring one, it hands to Alicia as a proposal she accepts
or rejects right where it sits.

We chose to read the code as it really is and let a model judge the attribution, and in exchange
we swallow the fact that the judgment is approximate and has to stay reviewable. The road we did
not take is the one the older systems took, holding prose and code together in a single, exactly
reversible form. Literate programming kept the explanation and the code in one source so each
could be spun out of the other; the round-trip tools that followed regenerated one side from the
other through a fixed grammar; and both turned brittle the instant real code stopped fitting the
mold. Our position is frankly a bet, that everyday code is regular enough for a model to
attribute most of the time, and that where the model is unsure it should offer a proposal rather
than guess in silence. The price of the bet is that this half is never guaranteed
correct, which is exactly why a change to the shape of the document is a proposal a person signs
off on rather than an edit slipped in behind their back, and why review is a first-class part of
the design instead of a bolt-on.

## Turning intent into code

The second half runs the other way, turning a change in the document into a change in the code;
and its central decision is a cautious one, that editing the document is documentation by default
and turns into a request to change code only through an explicit gesture we call a hand-off. When
a person edits a feature's description, CoDoc records the edit and quietly builds a held draft
alongside it, a plan for the code change that spells out what is wanted and then does absolutely
nothing until it is handed off. No code runs, nothing in the repository moves, and no model is
called, until the person decides the draft is ready to go.

Once Alicia has read the client and understood the bug, she edits the feature's description to
say that the client should retry when a request times out or fails, waiting a little longer after
each attempt and giving up after three tries, and that a test should cover the retry path; and
she drops in a link to the team's runbook on backoff. Her edit just sits on the feature as a held
draft. When she is ready she hands it off, and only then does CoDoc stitch an instruction
together out of the description, the bound code, and the runbook link, and set it in front of the
coding session she already has open in the same window. The agent does the work right there, in
her own session, hands back an ordinary diff, and the description it revised comes home to her as
tracked changes she can take or leave one at a time.

Two of the decisions buried in this half are worth saying out loud. The first is that the
hand-off is a deliberate gesture rather than a guess about how the person phrased their edit. We
actually built the guessing version once, scanning the prose for imperative wording, and then we
tore it out, because reading intent off phrasing gets it wrong in both directions at once,
mistaking a description that happens to open with a verb for a command and sailing right past a
real request written as a calm statement of fact. Letting a gesture decide means a person can
write whatever they want in the document without second-guessing whether some sentence will be
read as an order. The second decision is that the code gets written inside the person's own
coding session rather than in some separate automated one off to the side. That lets the very run
that writes the code also record which feature the code belongs to, so intent and attribution
stay stapled together through the change, and it keeps the document as plain, editable text. The
systems that made intent the primary artifact, intentional software and the projectional editors
that grew out of it, generated code from a structured model the person edited in place, and the
bill for that was that ordinary typing and version control stopped working, because there was no
text left to diff. We keep the person writing in a normal document and let the agent touch code
only after a hand-off, so every change still lands as a diff the person reviews. What we pay for
this is immediacy, since intent a person has already written down does not become code until they
come back and hand it off; and we would rather eat that delay than build a system that starts
rewriting the code the moment a description changes.

## When the document and the code disagree

Sooner or later the two halves reach for the same feature at the same instant. Alicia can be
halfway through rewriting a description just as the code side, having noticed a change in the
code, wants to update that very feature. CoDoc breaks the tie with a single rule of precedence,
the document wins. We could have settled it the way collaborative editors usually do, letting the
last writer win or blending both sides into one text; or we could have let the code side win on
the reasonable-sounding grounds that the code is what actually runs. We went with the document,
because the document is where intent is authored while the code side is only ever reporting what
the code happens to be doing; so when the two genuinely collide, the authored side is the one
worth keeping, and the observation the code side wanted to make will still be true and can be
made again on the next pass once the person has stepped away.

In practice, for as long as a feature carries an edit of Alicia's that she has not yet resolved,
that feature is held, and the code side holds off on anything it would have changed in
the feature's wording or structure, so a description she is in the middle of writing never gets
rewritten out from under her. The one thing the hold does not touch is binding maintenance,
because a binding only records where the code currently lives and says nothing about what the
feature means, so it stays correct even while the feature is held.

This rule can stay this simple only because intent and attribution were pulled apart from the
start. A binding carries no intent, so it is always safe to keep current; wording and structure
carry all of it, so they wait for the person who is editing them. The cost is that a held feature
can fall a little behind, since while Alicia's edit is unresolved the code side may not touch
that feature's wording or structure, so the document can stay exactly current about intent while
going briefly stale on detail until she resolves the edit. We take that trade gladly, because the
alternative is worse, a person losing a half-finished thought to the machine, and because the
staleness clears itself the second the hold lifts. What the person gets back for it is that the
reason for a change stays recorded exactly where the change was made, so the next person to open
the feature sees not just that the client now retries, but why anyone made it retry in the first
place.
