# Changelog

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
