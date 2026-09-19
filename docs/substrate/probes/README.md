# Probes

The adversarial reviews' own test files, kept as they were written. They are
**not** the suite: every property a probe found a fault in is in `tests/` under
a name that says the property, and a probe here may encode an expectation a
later round changed (each review's "Applied" note says which).

Keep them for one reason — a review's probe is written against the design as
the reviewer read it, so it is the record of what was actually checked. A
failure is a question to answer, not a regression.

Round 8 (the current round):

```
PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest docs/substrate/probes/probe8_core.py -q -p no:cacheprovider
PYTHONPATH=. ../CodeNav/.venv/bin/python -m pytest docs/substrate/probes/probe8_hub.py  -q -p no:cacheprovider
cd vscode-codoc && npx vitest run --config ../docs/substrate/probes/vitest.probe8.config.mjs
PYTHONPATH=. ../CodeNav/.venv/bin/python docs/substrate/probes/perf8.py      # the cost table in 19-… §C
```

`probe8_cost.txt` is the editor's measurements; the machine varies ±40% between
runs, so read ratios within one run.

Earlier rounds' probes are deleted once their properties are in the suite.
