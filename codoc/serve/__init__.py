"""The hub (`codoc serve`): the same editor, served to GitHub-authorized people
from the maintainer's machine.

It adds nothing to the format. A contributor's edits are submissions appended
to ``inbox/hub-<login>.jsonl``; the daemon stamps ``human:gh:<login>`` and applies
them under the role the hub recorded for that login (``roles`` in ``config.json``):
collaborators with push access are ``maintainer``, the rest ``suggest`` (plans
only). Acks and projections reach the browser over one SSE stream carrying the
same messages the extension host posts to its webview, so one bundle serves
both. Every route sits behind the session cookie once auth is configured; with
no auth the hub binds localhost and treats its one viewer as the owner.
"""
