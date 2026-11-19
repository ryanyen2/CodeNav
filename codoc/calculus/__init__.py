"""The codoc calculus: the attributed edit model of the document.

Spec: ``docs/model.md``. Everything in this package is pure
Python over in-memory structures; persistence (the log) and IO live in
``codoc.workspace``. Nothing here reads a file or a clock, and nothing here
imports the rest of codoc.

Module map:

- ``ids``:     op, character, block, plan and session identities.
- ``rank``:    fractional order keys for blocks.
- ``text``:    ``Text``: a sequence of attributed segments (live and dead) plus
               marks over character identity. Every text mutation lives here.
- ``doc``:     ``Block`` and the heading fold (features are a view).
- ``ops``:     the ``Op`` record and its wire form.
- ``store``:   the in-memory state the applier mutates.
- ``apply``:   ``apply(store, op)``: total, deterministic, O(touched blocks),
               records canonical payloads, inverses and conflicts.
- ``verdict``: ``plan.accept``, ``plan.reject`` and ``plan.reopen``, and the
               steps a verdict records.
- ``ingest``:  whole new text for one field → ops; the only module where a diff
               decides what changed.
- ``inline_md``, ``block_md``: the Markdown codecs for text and for blocks.
- ``derive``:  pure views over the store: what a feature says, who wrote which
               word, the three channels, the phase, the projection, the export.
- ``window``:  the timeline window: the last ops and the text around them.
"""
