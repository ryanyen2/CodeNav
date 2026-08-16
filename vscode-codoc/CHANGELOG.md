# Changelog

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
