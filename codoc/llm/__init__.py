"""Talking to a model.

- ``provider`` — which LLM, configured from the environment, and ``complete()``.
- ``prompt``   — loading and filling the templates in ``codoc/prompts``, and
                 parsing the JSON that comes back.
- ``passes``   — one function per prompt.
"""
