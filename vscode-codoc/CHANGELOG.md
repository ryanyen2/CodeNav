# Changelog

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
