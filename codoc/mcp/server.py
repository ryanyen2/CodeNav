"""codoc MCP server (FastMCP, stdio): the coding agent's surface.

Registered in ``<root>/.mcp.json`` by ``codoc init``. The ``.codoc`` directory
is found by walking up from the agent's cwd. Tools return dicts; a missing
workspace returns ``{"ok": False, "error": …}`` rather than raising.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

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
    and the PREFERRED read before editing or proposing. Pass repo-relative file paths
    and/or a feature_id: returns the features bound to those files expanded one
    hop along call/import edges, each with its paragraphs BY BLOCK ID (an amend
    names the paragraph it changes), a title outline for orientation, and nearby
    graph edges. Bounded by the edit, not the repo."""
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
    realizing | proposed), the features with queued work (`queued_list`, what
    /codoc:sync implements), features with unsent edits, open proposals, and
    citations that do not resolve to code."""
    cd, err = _need_dir()
    return err or tools.read_status(cd)


@mcp.tool
def codoc_history(feature_id: str, limit: int = 20) -> dict:
    """One feature's history: its ops newest-last (who, when, what) and the
    current attribution of every paragraph as runs (who wrote which words)."""
    cd, err = _need_dir()
    return err or tools.read_history(cd, feature_id, limit)


@mcp.tool
def codoc_reflect(ops: list[dict], rationale: str = "", caused_by: str | None = None) -> dict:
    """Reflect what you did to the code into the tree. Each op is one of:
    `{"kind": "attach"|"detach", "feature_id", "bindings": ["file.py::symbol", …]}`
    (lands at once); `{"kind": "amend", "feature_id", "title"?, "blocks": [{"id", "text"}
    | {"id", "delete": true} | {"text"}]}`, where `text` is one block as Markdown
    (a paragraph, a `- ` item, a `1. ` step, a table) and a block you do not list is
    unchanged (lands on the trunk when it only rewords a little and nobody has
    pending edits there, else as one plan); or `add_node` / `move_node` /
    `retire_node` (always a plan; accepting it takes the words, since the code is
    already written). Pass the directive id from realize.md as
    `caused_by` so the change is attributed to the request and closes it."""
    cd, err = _need_dir()
    return err or tools.reflect(cd, ops=ops, rationale=rationale, caused_by=caused_by, root_dir=_root(cd))


@mcp.tool
def codoc_propose(ops: list[dict], note: str = "", caused_by: str | None = None) -> dict:
    """Propose a plan the person accepts or rejects in the editor. Ops:
    `{"kind": "add_node", "id": "n1", "parent_id"?, "title", "description", "bindings"?}`
    (`description` is Markdown), `{"kind": "amend", "feature_id", "title"?, "blocks":
    [{"id", "text"} | {"id", "delete": true} | {"text"}]}` (a block you do not list is
    unchanged), `{"kind": "move_node", "feature_id", "parent_id"}`,
    `{"kind": "retire_node", "feature_id"}`. Each removal is its own plan, and a
    feature already proposed for removal is not proposed again. Accepting a plan
    from here queues the work for you; accepting a removal does not delete code."""
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
    'implementing n of m' and knows the pass is alive."""
    cd, err = _need_dir()
    return err or tools.realize_progress(cd, done=done, total=total, current=current)


@mcp.tool
def codoc_walkthrough(question: str, steps: list[dict], answer: str = "") -> dict:
    """Answer a question by drawing a numbered reading path over features that
    already exist. Steps: `{feature_id, note, quote?, group?, file?, symbol?, line?}`.
    Writes nothing to the tree."""
    cd, err = _need_dir()
    return err or tools.walkthrough(cd, question=question, answer=answer, steps=steps)


@mcp.tool
def codoc_walkthrough_clear() -> dict:
    """Dismiss the walkthrough on screen. Idempotent."""
    cd, err = _need_dir()
    return err or tools.clear_walkthrough_tool(cd)


@mcp.tool
def codoc_walkthrough_read() -> dict:
    """The walkthrough currently on the reader's screen, or null."""
    cd, err = _need_dir()
    return err or tools.read_walkthrough_tool(cd)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
