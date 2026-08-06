"""codoc MCP server (FastMCP, stdio).

Registered with Claude Code via ``<root>/.mcp.json`` (written by ``codoc init`` /
``install_hooks``). The agent in the code-first loop calls these tools to reflect
what it just did into the feature tree — carrying real intent that Loop A's blind
index-diff can only guess at. Every mutation routes through
:mod:`codoc.mcp.tools`, which uses the same ``apply_op`` + ``write_tree`` seam as
the rest of codoc, so identity/dedup/validation/rendering are reused.

The ``.codoc`` directory is resolved from the agent's cwd (where ``claude`` runs)
by walking up to the first ancestor containing ``.codoc`` — the same discovery
the hooks use. Tools return structured dicts; on a missing ``.codoc`` they return
``{"ok": False, "error": …}`` rather than raising, so the agent gets a clear
message instead of a tool crash.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from codoc.agent.paths import find_codoc_dir
from codoc.mcp import tools

mcp = FastMCP("codoc")


def _dir() -> str | None:
    return find_codoc_dir(os.getcwd())


def _need_dir() -> tuple[str | None, dict | None]:
    cd = _dir()
    if cd is None:
        return None, {"ok": False, "error": "no .codoc directory found from cwd — run `codoc init` first"}
    return cd, None


@mcp.tool
def codoc_context(files: list[str] | None = None, feature_id: str | None = None,
                  include_bindings: bool = True) -> dict:
    """The relevant slice of the feature tree for the code you are working on —
    the PREFERRED read before editing or proposing. Pass the repo-relative file
    path(s) you're editing (and/or a feature_id): returns the features bound to
    those files expanded one hop along call/import edges (with descriptions and
    bindings), a compact indented outline of every tree title for orientation,
    and nearby graph edges. Bounded by the edit, not the repo size — use
    codoc_tree only when you genuinely need the whole tree."""
    cd, err = _need_dir()
    return err or tools.read_context(cd, files=files, feature_id=feature_id,
                                     include_bindings=include_bindings)


@mcp.tool
def codoc_tree(root_id: str | None = None, depth: int = 0,
               include_bindings: bool = False) -> dict:
    """Read the feature tree (id, title, description, parent, realized, drift,
    binding_count, files) plus pending proposals. Read-only. For file-scoped
    work prefer codoc_context — it returns the relevant slice instead of
    everything.

    ``root_id`` limits to that subtree; ``depth`` (>0) caps levels;
    ``include_bindings=True`` adds every bound symbol_path (large — request it
    only when you need exact symbols; ``files``+``binding_count`` are always
    present).

    Each feature also carries ``drift`` — the last code-side pass's trust signal
    (``"questioned"`` = bound code changed but the prose wasn't amended;
    ``"binding-lost"`` = lost its last binding; ``null`` = followed). Amend or
    re-attach questioned features when reconciling."""
    cd, err = _need_dir()
    return err or tools.read_tree(cd, root_id=root_id, depth=depth,
                                  include_bindings=include_bindings)


@mcp.tool
def codoc_status() -> dict:
    """Counts of features / pending proposals / unrealized plan nodes, and the
    current pipeline state (in_sync | code_drift | tree_dirty | awaiting_impl |
    realizing). ``awaiting_impl`` means accepted tree edits are queued in
    ``.codoc/realize.md`` for you to implement via ``/codoc:sync``.

    Also reports ``dead_refs`` (count) + ``dead_ref_list`` ([{feature_id, file,
    symbol}]) — inline ``codoc:`` links that no longer resolve to a binding; fix or
    re-bind them when reconciling."""
    cd, err = _need_dir()
    return err or tools.read_status(cd)


@mcp.tool
def codoc_history(feature_id: str, limit: int = 20) -> dict:
    """One feature's change history (blame): who changed it, when, how, and
    why — actor/mode/caused_by per applied event, with the title/description
    snapshots amends left behind. Use before reworking a feature to understand
    the intent already invested in it."""
    cd, err = _need_dir()
    return err or tools.feature_history(cd, feature_id, limit=limit)


@mcp.tool
def codoc_reflect(ops: list[dict], rationale: str = "", caused_by: str = "") -> dict:
    """Submit the whole set of tree changes implied by code you just wrote, in one
    call. This is the primary code-first reflection entrypoint.

    Each op: {kind, feature_id?, parent_id?, title?, description?, binds?,
    rationale?, caused_by?}. kind ∈ attach|detach|refresh|amend|add_node|move_node|retire_node.
    binds are "file.py::symbol_path" strings. Safe ops (attach/refresh/detach and
    small amends) apply immediately; structural ops become proposals the user
    reviews. Prefer `attach` to an existing feature over `add_node`.

    When implementing a ``.codoc/realize.md`` directive, pass its ``⟨d-…⟩`` id as
    ``caused_by`` — the IDE uses it to group your reflected changes under the doc
    edit that requested them."""
    cd, err = _need_dir()
    return err or tools.reflect(cd, ops=ops, rationale=rationale, caused_by=caused_by)


@mcp.tool
def codoc_propose_add(title: str, description: str = "", parent_id: str | None = None,
                      binds: list[str] | None = None, rationale: str = "",
                      caused_by: str = "") -> dict:
    """Propose a NEW feature for code no existing node covers (a reviewable
    proposal). Set parent_id from `codoc_tree` to nest it; binds are
    "file.py::symbol_path"."""
    cd, err = _need_dir()
    return err or tools.propose_add(cd, title=title, description=description,
                                    parent_id=parent_id, binds=binds, rationale=rationale,
                                    caused_by=caused_by)


@mcp.tool
def codoc_propose_amend(feature_id: str, title: str | None = None,
                        description: str | None = None, rationale: str = "",
                        caused_by: str = "") -> dict:
    """Propose editing a feature's title and/or description (e.g. its meaning
    shifted). Small description edits apply immediately; larger ones are reviewed."""
    cd, err = _need_dir()
    return err or tools.propose_amend(cd, feature_id=feature_id, title=title,
                                      description=description, rationale=rationale,
                                      caused_by=caused_by)


@mcp.tool
def codoc_propose_move(feature_id: str, parent_id: str | None, rationale: str = "",
                       caused_by: str = "") -> dict:
    """Propose reparenting a feature (restructure). parent_id=null moves it to the
    top level. Reviewable."""
    cd, err = _need_dir()
    return err or tools.propose_move(cd, feature_id=feature_id, parent_id=parent_id,
                                     rationale=rationale, caused_by=caused_by)


@mcp.tool
def codoc_propose_retire(feature_id: str, rationale: str = "", delete_code: bool = False,
                         caused_by: str = "") -> dict:
    """Propose retiring a feature. Reviewable.

    delete_code=False (default): detach-only — accepting untracks the feature but
    keeps its code. delete_code=True: also queue a code-removal directive on accept
    (the agent-side parity for a human `~` retire) — use only when the bound code
    should genuinely be deleted, not merely untracked."""
    cd, err = _need_dir()
    return err or tools.propose_retire(cd, feature_id=feature_id, rationale=rationale,
                                       delete_code=delete_code, caused_by=caused_by)


@mcp.tool
def codoc_attach(feature_id: str, binds: list[str], rationale: str = "",
                 caused_by: str = "") -> dict:
    """Bind code chunks ("file.py::symbol_path") to an EXISTING feature. ATTACH is
    safe → applied immediately (no review). Binding the first code to a plan
    placeholder flips it to realized."""
    cd, err = _need_dir()
    return err or tools.attach(cd, feature_id=feature_id, binds=binds, rationale=rationale,
                               caused_by=caused_by)


@mcp.tool
def codoc_realize_progress(done: int, total: int, current: str = "") -> dict:
    """Report progress while implementing ``.codoc/realize.md`` directives so the
    IDE shows "implementing N of M". Call it as you start each directive: pass the
    number completed so far, the total directive count, and the current title."""
    cd, err = _need_dir()
    return err or tools.realize_progress(cd, done=done, total=total, current=current)


@mcp.tool
def codoc_plan_add(title: str, description: str = "", parent_id: str | None = None,
                   binds: list[str] | None = None, rationale: str = "") -> dict:
    """Propose a PLAN placeholder node (used by /codoc:plan, before writing code).
    Accepted, it enters the tree as an unrealized placeholder until code binds to
    it. Do NOT edit code in the planning step."""
    cd, err = _need_dir()
    return err or tools.plan_add(cd, title=title, description=description,
                                 parent_id=parent_id, binds=binds, rationale=rationale)


@mcp.tool
def codoc_await_verdicts(event_ids: list[str], timeout: float = 86400.0) -> dict:
    """BLOCK until the user Accepts/Rejects the given proposals in the codoc IDE.

    The realization trigger for /codoc:plan: after proposing plan nodes, call this
    with their event_ids instead of ending the turn. It waits (polling the IDE's
    inbox) and applies each verdict as it lands — accept makes the placeholder live,
    reject discards it — then returns {accepted:[{event_id,feature_id,title}],
    rejected, pending, timed_out}. Continue the SAME turn to implement the accepted
    nodes and bind code via codoc_attach/codoc_reflect."""
    cd, err = _need_dir()
    return err or tools.await_verdicts(cd, event_ids=event_ids, timeout=timeout)


@mcp.tool
def codoc_plan_status() -> dict:
    """Report which plan placeholders are still unrealized vs realized — the
    plan-satisfaction check after implementing."""
    cd, err = _need_dir()
    return err or tools.plan_status(cd)


def main() -> None:
    """Console entrypoint (``codoc-mcp``). Runs the stdio server."""
    mcp.run()


if __name__ == "__main__":
    main()
