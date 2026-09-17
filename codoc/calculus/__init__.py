"""The codoc calculus — the attributed edit substrate.

Spec: ``docs/substrate/01-calculus.md``. Everything in this package is pure
Python over in-memory structures; persistence (the log, the SQLite cache) and
IO live in ``codoc.substrate``. Nothing here reads a file or a clock.

Module map:

- ``ids``    — op, character, block, plan and session identities.
- ``rank``   — fractional order keys for blocks.
- ``text``   — ``Text``: a sequence of attributed segments (live and dead) plus
               marks over character identity. Every text mutation lives here.
- ``doc``    — ``Block`` and the heading fold (features are a view).
- ``ops``    — the ``Op`` record and its wire form.
- ``store``  — the in-memory state the applier mutates.
- ``apply``  — ``apply(store, op)``: total, deterministic, O(touched blocks),
               records canonical payloads, inverses and conflicts.
"""
