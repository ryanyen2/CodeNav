"""The loops that join the code and the document.

- ``context``:    the subtree, title outline and graph sketch an LLM pass or an
                  agent read is shown (ego-graph selection over bindings).
- ``bootstrap``:  the first tree, built from the code.
- ``reflect``:    code → document: deterministic binding maintenance, then one
                  LLM pass whose answer enters through ``ingest`` and plans.
- ``realize``:    document → code: the hand-off, the queue of directives it
                  produces, and the realization watermark when the work lands.
- ``translate``:  rewrite an existing tree into another authoring language.
"""
