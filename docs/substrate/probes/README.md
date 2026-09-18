# Probes

The adversarial reviews' own test files, kept as they were written. They are
**not** the suite: every property a probe found a fault in is in `tests/` under
a name that says the property, and a probe here may encode an expectation a
later round changed (each review's "Applied" note says which).

Keep them for one reason — a review's probe is written against the design as
the reviewer read it, so it is the record of what was actually checked. Run one
with `PYTHONPATH=. pytest docs/substrate/probes/probe7_core.py`; a failure is a
question to answer, not a regression.

- `probe7_core.py`, `probe7_hub.py`, `probe7_editor.test.ts` — round 7.
- `perf7.py` — the cost table in `17-…` §C. `PYTHONPATH=. python docs/substrate/probes/perf7.py`.

Rounds 2–6's probes were deleted once their properties were in the suite.
