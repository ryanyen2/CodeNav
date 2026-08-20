# Changelog

## 0.2.10

### Fixed

- **The replay left the workspace claiming an agent was still working.** Every surface
  keyed on that stayed on long after the session ended: the presence avatar, "running
  pytest" in the status, and — the one people noticed — the realize ghost, which dims a
  feature's prose to the faint ink while an agent is still writing it. A participant took
  the handover and found the document greyed out, narrating work that had finished
  minutes earlier. The recording now ends its turn where the agent actually stops: at the
  end, and at each checkpoint. Ending it at a checkpoint matters on its own, because the
  second stop exists for reading what the build did to the descriptions, and leaving the
  turn open there ghosted the very sentence it was asking about.

- **Replaying deleted the participant's launcher.** The reset removes whatever the
  recorded starting state does not contain, which is right for code and wrong for the
  files setup.sh puts in the folder for the person using it. `claude-study` was one of
  them, so the first thing anybody did after the recording — go back to the terminal and
  start the agent again — answered "no such file or directory", mid-session.

- **The workspace check asserted a fixture count that a new fixture had made stale**, so
  setup failed on a workspace that was correct. It counts what is in `fixtures/` now.


## 0.2.9

### Fixed

- **Deleting a node did nothing, and said nothing.** Selecting a heading in the doc view
  and deleting it emitted no command at all: the store never heard, and the next
  projection drew the node straight back. Editing the raw `tree.codoc` looked like it
  worked and did not — that file is a read-only export the daemon rewrites, so the
  deletion sat in the buffer until it was overwritten. Absence retires now.

  It was suppressed on purpose. A heading vanishes for reasons that are not deletions —
  a backspace at the start of one merges it into the block above, select-all-delete takes
  every heading for a frame, a cut is gone until its paste lands — and inferring
  destruction from any of them detaches a feature's bindings for a keystroke. Those cases
  are now told apart rather than lumped together: a deletion has to hold across two
  settles, must not take more than half the tree at once, and must take the heading's
  words with it rather than fold them into a neighbour. Deleting a title character by
  character still retires, because it is a deletion. `~ retire` still works and is now
  the way to retire a node you want to keep reading while you decide.

- **A verdict could go nowhere, permanently, with no way back.** The daemon was started on
  three events — the window activating, the workspace being trusted, a replay handing the
  files back — and never asked about again. Missing any one of them is silent: a folder
  opened untrusted grants trust before the extension loads, a lock is created and deleted
  while the window is still starting, a file watcher drops an event. The symptom was the
  same in every case and named none of them, `Verdict not picked up`, with nothing to do
  about it but quit. The daemon is now re-checked on every `.codoc/` change and on a
  timer, and backs off rather than respawning one that cannot start. The notice says
  `codoc is not running` and carries a **Start codoc** button; the same thing is on the
  palette as `codoc: Start the daemon`.

- **A proposed node moved when you accepted it.** The editor draws a proposed node at the
  rank it would take, immediately before its anchor — and the store then filed it first,
  because naming only what a node goes *before* meant "put it at the top". Two nodes
  proposed before the same anchor also landed on the same rank, which is not an order at
  all. `before` now means immediately before; putting a node first is still said by naming
  the node it goes before.


## 0.2.7

Fixes reported from using 0.2.6, and the study instrument that goes with them.

### Fixed

- **A comment you sent was invisible.** The thread's card only rendered where the right
  margin happened to be wide enough to hold one, which at most real window widths it is
  not, so the usual outcome of commenting was a highlighted phrase with nothing behind
  it. The only tooltip in reach belonged to the hold rail, which read as though the note
  had been swallowed by the queue. The anchored span now carries a marker of its own.
  Hover it to read the thread, click to keep it open. The marker is the surface and the
  margin card is the optional part.

- **A thread can answer now.** When its directive lands, the agent's reply is appended to
  it and names the files the work actually touched, built from the ops that cite the
  directive rather than from a summary, so it is a claim the ledger can stand behind.
  Before this a comment could only change colour, and the author had to go elsewhere to
  learn whether their note had been acted on.

- **The `/codoc:ask` notes covered the text they were helping you read.** They were drawn
  at a fixed right inset whatever the window width. The ordinal chip beside each heading
  is already the durable signal that a stop is there, so the note behind it is one hover
  away and never occupies the page.

- **The comment composer stayed pinned while the document scrolled underneath**, still
  claiming to annotate text that had moved away. It is positioned in the surface's scroll
  space now, so it travels with its sentence.

- **Accepting a plan no longer queues work with nobody assigned to it.** An accept with no
  live session offers to run it, and the status bar says "queued, not running" rather than
  implying something is already under way.

## 0.2.6

The tree can now show you its own past — and several controls stopped lying about what
they do.

### Added

- **History, in place.** The History stance grows a **timeline** above the document.
  Drag it back and the page re-reads as it did at that moment, with that moment's change
  marked in the prose where it happened — old words struck, new underlined. It is
  reconstructed in the editor from a bounded window of applied events
  (`.codoc/revisions.json`), each carrying the text it displaced, so scrubbing is local
  and instant. A change the ledger cannot account for is reported as unreconstructible
  rather than diffed against invented words.

- **Why does this sentence say this?** One hover card — reachable from a moment on the
  timeline or from a feature's own History label — walks the chain the change ledger has
  always held and never showed together: the change, the directive that asked for it, the
  prompt somebody typed, the session they typed it in, the commit the code work started
  from, and **the code diff itself**, opened against that commit. Directives now record
  their prompt, session and git anchor to make this exact.

- **Blame is per sentence, not per feature.** "claude-code edited this 3h ago" answers a
  question nobody asks; a feature is several paragraphs written by three parties in turn,
  and the reader is deciding whether to trust one *claim*. Authorship is now derived per
  span by replaying the revision window's word diffs. A span the ledger cannot account
  for stays unattributed, and prose with a single author is left completely clean.

- **A comment is a work order.** It scopes itself to the code its sentence cites (no
  picker — descriptions already cite their code), can ask for the description to follow
  the code as well, records the directive it produced, and reports **done** when that
  lands, with the code it caused one click away. **Build it** sends the note and starts
  the agent immediately (`codoc: Implement queued changes now`).

### Fixed

- **Bold never reached the agent.** Bolding a phrase is supposed to make it the
  highest-priority part of the intent (`Focus:` on the realize directive) — the flagship
  markdown-native signal. It has never worked from the editor: the button produced a mark
  that was discarded on save, and typing literal `**` was converted to the same doomed
  mark. Bold now round-trips, and the tooltip says what it causes.

- **Italic and Highlight silently discarded your work.** Both produced marks that were
  dropped on save and wiped by the next projection. Removed.

- **The `◇ plan` button made an ordinary feature.** Its `realized` flag never reached the
  daemon, so the promised build request was never minted. Wired through; the tooltip now
  describes what actually happens.

- **Your own conflicted edit was handed back to you as "from code".** When two parties
  edit the same lines, your text is held for review — and the surface attributed it to
  the codebase. It now reads as yours. A pass whose only outcome was that deferral also
  wrote no sidecar, so the edit reached no surface at all until some unrelated pass
  happened to re-render.

- **Comments no longer move the document.** Opening the margin used to slide the whole
  prose column sideways — on *arrival* as well as authoring, so somebody else's comment
  yanked your page mid-read, and writing one fired it three times. At most window widths
  it was not a slide but a full re-wrap, and the transition meant to soften it named a
  property nothing ever changed. Cards now hang in the whitespace that exists; where
  there is none they become popovers. Hovering either the commented words or their card
  lights both.

- **A resolved comment could not be dismissed.** It stayed in the sidecar forever, so
  every projection brought the card back, and its anchor kept underlining prose that no
  longer had a thread behind it. Resolved threads now leave the margin after a while; the
  record itself is kept.

- **Chinese prose read as stiff 書面語.** Chinese was the only CJK language with no
  register rule at all (Japanese is told である体, Korean 해라체), while the translate
  prompt asked for "same order, same number of sentences" — an instruction to keep English
  clause order, which is what translationese is. Both fixed.

- **Onboarding taught a retired mechanism.** The walkthrough's one line of interaction
  guidance described `> …` steering notes, which stopped working in U7.

- A rewrite awaiting Keep/Restore now stands down once you have edited that feature
  yourself: its "Restore mine" baseline is stale by then, and restoring would have
  discarded your newer words.

## 0.2.5

Two ways to *read* the tree, where before there was only editing it.

### Added

- **`/codoc:ask` — a question, answered in the tree.** Ask the agent how something
  works and instead of a paragraph in chat it draws a numbered reading path over the
  features that already hold the answer: a step number beside each heading, a quiet
  highlight on the sentence it is pointing at, and — like a comment — a card in the
  right whitespace carrying the note for that stop. It writes nothing to the tree — it
  is a pure overlay (`.codoc/ask.json`), safe to raise mid-edit and gone when
  dismissed — so it never collides with a draft, a proposal, or a rewrite in flight.

- **Find & replace in the Codoc Tree (`Cmd+F` / `Cmd+Alt+F`).** The tree editor is a
  webview, which gets no native Find widget, and `tree.codoc` is a read-only export
  you cannot edit directly — so searching or renaming across the tree had nowhere to
  happen. Now `Cmd+F` searches titles and descriptions (case / whole-word / regex),
  and replace routes through the same path as typing, so a rename is an ordinary edit
  the daemon reconciles, not a back-door write.

### Fixed

- **The find widget showed itself, sat over the prose, and would not close.** Its
  own `display` rule beat the browser's `[hidden]` rule, so it rendered from the
  moment the editor mounted — never actually invoked — which also left its `✕`
  inert (the close guard saw a widget that had never been opened). It now appears
  only on `Cmd+F`, closes on `✕`/`Esc`, and reserves no space when shut.

- **The `/codoc:ask` note no longer reflows the document.** It used to insert a line
  between a heading and its prose, pushing the description down; a walkthrough that
  can stay up for a whole reading session should not compress the tree it is a
  reading order *over*. The note moved to the right whitespace as a margin card,
  anchored to the highlighted sentence, and the walkthrough's step numbers now also
  show on the left navigator so the reading path reads in the collapsed tree view.

> **Study note.** Both surfaces are on by default in the codoc arm, so they change
> the tool the study measures: sessions run before this cut-over are not comparable
> on the task-1 (understanding) timing, and the codoc arm must be described as it now
> stands. The study logger records a codoc-only `ASK` action (a count of stops, never
> the question) so walkthrough use is visible within the arm.

## 0.2.1

The release the user study actually runs on. 0.2.0 could not have collected a
usable session.

### Fixed

- **A participant's second condition mirrored nothing.** Somebody works in two
  workspaces, so the logger writes two log files and two state files, and the
  sign-in identity lived in those. The second condition signed in as a new
  anonymous user, could not claim the one device slot the first had taken, and
  sent nothing — half of every participant's data, silently, behind a message on
  an output channel nobody has open. Identity is now one file per machine; the
  read offset stays per log, because that is about the log. An older layout is
  still honoured, so a machine set up before this keeps the slot it holds.

- **Nothing could release a code.** The logger tells a participant to ask the
  experimenter to release theirs, and there was no way to do it. A participant
  who changed machine or reinstalled was locked out for the rest of the study.

- **codoc could not use gpt-5.6-luna at all.** Every call carried a temperature
  and that model accepts only its own default, so every call failed. A
  temperature is now omitted when asked for, and a refusal is learned from the
  API rather than from a list of model names that goes stale.

### Changed

- The study's models are pinned per workspace rather than inferred, so a key in
  a participant's own shell cannot move codoc onto their account, spend their
  money, or break partway through the condition being measured.

## 0.2.0

The release the user study runs on.

### Fixed

- A feature could end up looking attributed while owning no code. Bindings
  proposed by a model are now checked before they are written, both for shape
  (every chunk is addressed `file::symbol`, so a path that has lost its own
  basename can never match) and, where the caller can see the index, for whether
  the symbol exists at all. What cannot be addressed is dropped, and saying so
  on stderr, rather than dangling forever out of reach of the repair path.
- Accepting a proposal in a workspace whose index could not be read dropped
  every binding on it, because "no index" and "an empty index" were the same
  value. They are now distinct, and no index falls back to the shape check.
- A reply that would not parse used to take a whole reflection pass with it,
  losing the safe work already done and re-deriving it on the next pass. The
  safe work now stands and the unbound code is picked up next time.
- An MCP `bind` with no `::` named a file rather than a chunk and was stored as
  a binding that could never match anything. It is refused instead.

### Added

- Token counts are recorded per call into a snapshot-able total, so the cost of
  a pass can be measured. `llm_calls` now reports every call rather than only
  the batched ones.

## 0.1.1

Authoring language selection, auto-edit decorations, and the packaging that
installs the Python core through `uv` with no key and no terminal.
