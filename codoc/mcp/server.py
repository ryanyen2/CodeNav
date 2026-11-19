"""codoc MCP server (FastMCP, stdio): the coding agent's surface.

Registered at Claude Code's local scope, for this machine, by ``codoc
install-hooks``. The ``.codoc`` directory is found by walking up from the
agent's cwd. Tools return dicts; a missing
workspace returns ``{"ok": False, "error": …}`` rather than raising.
"""
from __future__ import annotations

import os
import signal

from fastmcp import FastMCP

from codoc.daemon import client, reads
from codoc.workspace.layout import find_codoc_dir
from codoc.mcp import tools

mcp = FastMCP("codoc")


def _need_dir() -> tuple[str | None, dict | None]:
    cd = find_codoc_dir(os.getcwd())
    if cd is None:
        return None, {"ok": False, "error": "no .codoc directory found from cwd. Run `codoc init` first"}
    return cd, None


def _root(cd: str) -> str:
    return os.path.dirname(cd)


@mcp.tool
def codoc_context(files: list[str] | None = None, feature_id: str | None = None, include_bindings: bool = True) -> dict:
    """The relevant slice of the feature tree for the code you are working on,
    and the read to make before editing or proposing. `files` (repo-relative):
    the features bound to them expanded one hop along call/import edges.
    `feature_id`: that feature, the features under it, and the features owning
    code it calls or that calls it. Each feature comes with its paragraphs by block id (an
    amend names the paragraph it changes), plus a title outline and nearby graph edges."""
    cd, err = _need_dir()
    return err or tools.read_context(cd, files=files, feature_id=feature_id, include_bindings=include_bindings)


@mcp.tool
def codoc_tree(root_id: str | None = None, depth: int = 0, include_bindings: bool = False) -> dict:
    """The whole tree (id, title, paragraphs by block id, parent, phase, files)
    plus open plans. Read-only. Prefer codoc_context for file-scoped work."""
    cd, err = _need_dir()
    return err or tools.read_tree(cd, root_id=root_id, depth=depth, include_bindings=include_bindings)


@mcp.tool
def codoc_status() -> dict:
    """Where the tree and the code stand: `state` (synced | edited | queued |
    realizing | reported | proposed), the hand-offs left to build
    (`queued_list`, what /codoc:realize builds, each with its `handoff` id),
    the hand-offs this turn reported with codoc_reflect (`reported_list`: they
    settle when your turn ends, so there is nothing to wait for), features with
    unsent edits, the number of open plans, and citations that do not resolve
    to code."""
    cd, err = _need_dir()
    return err or reads.queue_status(cd)


@mcp.tool
def codoc_history(feature_id: str, limit: int = 20, ops: bool = False) -> dict:
    """One feature's history: its last `limit` changes newest-last, one row per
    batch (a gesture, an ingest or a plan) with when, who, the plan, the
    hand-off it answers and a one-line summary; `ops` lists each row's ops too.
    Then the current attribution of every paragraph as runs (who wrote which
    words)."""
    cd, err = _need_dir()
    return err or reads.read_history(cd, feature_id, limit, ops=ops)


@mcp.tool
def codoc_reflect(ops: list[dict], rationale: str = "", caused_by: str | None = None) -> dict:
    """Reflect what you did to the code into the tree. Each op is one of:
    `{"kind": "attach"|"detach", "feature_id", "bindings": ["file.py::symbol", …]}`
    (lands at once; a binding must name code the files define now, new code
    included; a symbol the feature already owns is refreshed when you changed
    its code, and skipped when you did not); `{"kind": "amend", "feature_id", "title"?, "blocks": [{"id", "text"}
    | {"id", "delete": true} | {"text"}]}`, where `text` is one block as Markdown
    (a paragraph, a `- ` item, a `1. ` step, a table) and a block you do not list is
    unchanged (lands on the trunk when it only rewords a little and nobody has
    pending edits there, else as one plan); or `add_node` / `move_node` /
    `delete_node` (always a plan; accepting it takes the words, since the code is
    already written). `rationale` is one plain line saying what the edits change
    in the document; it is the note on any plan they open. Describe the code as it
    is, not what changed: no "now", "no longer" or "updated to", in the rationale or
    in amended text. Pass the hand-off's id from realize.md (`d:…`) as
    `caused_by`: that reports the hand-off, which settles when your turn ends."""
    cd, err = _need_dir()
    return err or tools.reflect(cd, ops=ops, rationale=rationale, caused_by=caused_by, root_dir=_root(cd))


@mcp.tool
def codoc_propose(ops: list[dict], note: str = "", caused_by: str | None = None) -> dict:
    """Propose a plan the person accepts or rejects in the editor. Ops:
    `{"kind": "add_node", "id": "n1", "parent_id"?, "title", "description", "bindings"?}`
    (`description` is Markdown; `bindings` only for code that already exists),
    `{"kind": "amend", "feature_id", "title"?, "blocks":
    [{"id", "text"} | {"id", "delete": true} | {"text"}]}` (a block you do not list is
    unchanged), `{"kind": "move_node", "feature_id", "parent_id"}`,
    `{"kind": "delete_node", "feature_id"}`. `note` is one plain line saying what
    the plan changes in the document, describing the code as it will be, not what
    changes: no "now", "no longer" or "updated to", in the note or in amended text.
    Each deletion is its own plan, and a
    feature already proposed for deletion is not proposed again. Accepting a plan
    from here queues the work for you; accepting a deletion does not delete code."""
    cd, err = _need_dir()
    return err or tools.propose(cd, ops=ops, note=note, caused_by=caused_by, root_dir=_root(cd))


@mcp.tool
def codoc_write_block(content: dict, bid: str | None = None, kind: str | None = None, feature_id: str | None = None,
                      plan: str | None = None, caused_by: str | None = None) -> dict:
    """Write content to a media block (diagram, image, url, pdf, screenshot,
    comment) or add one at the end of a feature (`kind` + `feature_id`). Fields
    typed Text (a diagram's `source`, an image's `alt`/`caption`) are Markdown
    and are attributed to you word by word; the rest is replaced whole. Pass
    `plan` to propose instead of write."""
    cd, err = _need_dir()
    return err or tools.write_block(cd, content=content, bid=bid, kind=kind, feature_id=feature_id, plan=plan,
                                    caused_by=caused_by, root_dir=_root(cd))


@mcp.tool
def codoc_realize_progress(done: int, total: int, current: str = "") -> dict:
    """Report progress through the realize queue so the editor shows
    'building n of m' and knows the pass is alive. Returns the hand-offs this
    turn reported (`reported`), which settle when your turn ends."""
    cd, err = _need_dir()
    return err or tools.realize_progress(cd, done=done, total=total, current=current)


def main() -> None:
    try:
        tools.agent_client()       # a CODOC_AGENT that cannot name an inbox file refuses to start
    except ValueError as e:
        raise SystemExit(str(e)) from None

    # Claude Code stops a stdio server with SIGINT and then SIGTERM about 100 ms
    # later, before the event loop has unwound, and SIGTERM's default skips
    # atexit. A tool runs in a worker thread, and `release` waits for the
    # batch it is applying, so the log never ends halfway through one. The
    # stop runs once, and `os._exit` skips
    # interpreter shutdown, which aborts on stdin's buffer lock while FastMCP's
    # reader thread is blocked reading it.
    def stop(*_):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        client.release()
        os._exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    mcp.run()


if __name__ == "__main__":
    main()
