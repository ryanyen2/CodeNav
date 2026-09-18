"""The hub (`codoc serve`): the same editor, served to GitHub-authorized people
from the maintainer's machine (`04-version-control.md` §6, `06-cut-list.md` §1).

It adds nothing to the format. A contributor's edits are submissions appended
to ``inbox/hub-<login>.jsonl``; the daemon stamps ``human:<login>`` and applies
them under the role the hub recorded for that login (``cache/roles.json``):
collaborators with push access are ``maintainer``, the rest ``suggest`` (plans
only). Acks and projections reach the browser over one SSE stream carrying the
same messages the extension host posts to its webview, so one bundle serves
both. Every route sits behind the session cookie once auth is configured; with
no auth the hub binds localhost and treats its one viewer as the owner.
"""
