"""A comment as a unit of requested work (W8).

Before this, an inline comment was a note that lived in extension-host memory, produced
a directive scoped to every file its feature touched, and could never report what became
of it. These tests pin the three things that changed:

  1. the thread is DURABLE (it survives the tab that authored it);
  2. it can name the code it means, and that narrows what the agent may edit;
  3. its lifecycle closes — sent → resolved — when the work actually lands.
"""
from __future__ import annotations

import pytest

from codoc.loop import edits as edits_channel
from codoc.loop.apply import apply_op
from codoc.loop.loop_b import build_steer_directive, run_loop_b
from codoc.model.annotation import CommentScope, CommentStatus
from codoc.model.binding import Binding
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    codoc_dir = root / ".codoc"
    codoc_dir.mkdir(parents=True)
    store = open_store(codoc_dir)
    yield root, codoc_dir, store
    store.close()


def _feature(store, title="Uploads", description="Accepts files.", files=()):
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=description)
    apply_op(op, store, source="user", applied=True)
    for f, sym in files:
        store.upsert_binding(Binding(feature_id=op.feature_id, file=f, symbol_path=sym,
                                     fingerprint=""))
    return op.feature_id


# ── the directive a comment produces ─────────────────────────────────────────

def test_named_code_narrows_what_the_agent_may_edit(repo):
    """The difference between commenting ON something and commenting NEAR it.

    Without named targets a one-line note licenses edits across every file the feature
    touches — which is how "fix the retry in the uploader" becomes a subsystem rewrite.
    """
    _root, _cd, store = repo
    fid = _feature(store, files=[("upload.py", "upload.py::handle"),
                                 ("storage.py", "storage.py::put"),
                                 ("api.py", "api.py::route")])

    broad = build_steer_directive(fid, "back off exponentially", store)
    broad_scope = next(l for l in broad.splitlines() if l.startswith("  Edit only:"))
    assert {"upload.py", "storage.py", "api.py"} == set(
        broad_scope.removeprefix("  Edit only:").replace(" ", "").split(","))

    narrow = build_steer_directive(fid, "back off exponentially", store,
                                   code_refs=["upload.py::handle"])
    assert "Edit only: upload.py" in narrow
    assert "storage.py" not in narrow


def test_a_comment_with_no_named_code_keeps_the_feature_scope(repo):
    _root, _cd, store = repo
    fid = _feature(store, files=[("upload.py", "upload.py::handle")])
    assert "Edit only: upload.py" in build_steer_directive(fid, "note", store, code_refs=[])


def test_the_anchored_sentence_rides_along(repo):
    """A note reads as a reply. Without the words it replies TO, the agent has to guess
    which claim is being corrected."""
    _root, _cd, store = repo
    fid = _feature(store)
    d = build_steer_directive(fid, "should back off exponentially", store,
                              anchor_text="Retries three times on failure.")
    assert 'About this line: "Retries three times on failure."' in d


def test_scope_both_asks_for_the_prose_to_follow(repo):
    _root, _cd, store = repo
    fid = _feature(store)
    code_only = build_steer_directive(fid, "note", store)
    assert "update this feature's description" not in code_only

    both = build_steer_directive(fid, "note", store, scope="both")
    assert "update this feature's description to match" in both
    assert "codoc_reflect" in both


def test_code_only_is_the_default(repo):
    """The conservative reading: change exactly what the author pointed at."""
    _root, _cd, store = repo
    fid = _feature(store)
    assert "update this feature's description" not in build_steer_directive(fid, "n", store)


# ── the thread survives its author's tab ─────────────────────────────────────

def test_a_drained_steer_persists_its_thread(repo):
    _root, codoc_dir, store = repo
    fid = _feature(store, files=[("upload.py", "upload.py::handle")])
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text='re "Accepts files.": rate-limit this',
        comment_id="cm-1", body="rate-limit this",
        anchor_text="Accepts files.", code_refs=["upload.py::handle"], scope="both"))

    run_loop_b(str(_root), str(codoc_dir))

    threads = store.comments_for_feature(fid)
    assert len(threads) == 1
    t = threads[0]
    assert t.id == "cm-1"
    assert t.body == "rate-limit this"
    assert t.anchor_text == "Accepts files."
    assert t.code_refs == ["upload.py::handle"]
    assert t.scope is CommentScope.BOTH
    # SENT, not OPEN: the steer reaching the queue IS the hand-off.
    assert t.status is CommentStatus.SENT


def test_a_thread_records_the_directive_its_note_became(repo):
    """The join that closes the loop: thread → directive → the events it caused."""
    _root, codoc_dir, store = repo
    fid = _feature(store, files=[("upload.py", "upload.py::handle")])
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="rate-limit this", comment_id="cm-1", body="rate-limit this"))

    run_loop_b(str(_root), str(codoc_dir))

    t = store.comments_for_feature(fid)[0]
    assert t.directive_id.startswith("d-")
    assert any(d.id == t.directive_id for d in edits_channel.peek_manifest(codoc_dir))


def test_editing_a_note_replaces_its_pending_steer(repo):
    """Identity is the comment id. Re-handing an edited note must not queue a second
    directive for the same thread — every save of a comment body used to cost the agent
    another item in its queue."""
    _root, codoc_dir, _store = repo
    for body in ("first take", "second take"):
        edits_channel.append_steer(codoc_dir, edits_channel.Steer(
            feature_id="f-1", text=body, comment_id="cm-1", body=body))
    pending = edits_channel.read_steers(codoc_dir)
    assert len(pending) == 1
    assert pending[0].text == "second take"


def test_a_steer_with_no_comment_id_still_appends(repo):
    """The CLI path has no thread identity to dedup on."""
    _root, codoc_dir, _store = repo
    for body in ("one", "two"):
        edits_channel.append_steer(codoc_dir, edits_channel.Steer(feature_id="f-1", text=body))
    assert len(edits_channel.read_steers(codoc_dir)) == 2


# ── the lifecycle closes ─────────────────────────────────────────────────────

def test_resolving_keeps_the_record_rather_than_deleting_it(repo):
    """A resolved comment is the durable answer to "why does this code look like this".
    Deleting it on close throws that away at the moment it becomes history."""
    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="do the thing", comment_id="cm-1", body="do the thing"))
    run_loop_b(str(_root), str(codoc_dir))

    edits_channel.append_comment_resolve(codoc_dir, "cm-1")
    run_loop_b(str(_root), str(codoc_dir))

    threads = store.comments_for_feature(fid)
    assert len(threads) == 1
    assert threads[0].status is CommentStatus.RESOLVED
    assert threads[0].body == "do the thing"


def test_a_thread_resolves_itself_when_its_directive_lands(repo):
    """The half of the lifecycle that never existed: a note went out and nothing came
    back, so "sent" was the last thing a thread could say whatever the agent did."""
    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="do the thing", comment_id="cm-1", body="do the thing"))
    run_loop_b(str(_root), str(codoc_dir))
    directive_id = store.comments_for_feature(fid)[0].directive_id
    assert directive_id

    edits_channel.log_realized(codoc_dir, [
        edits_channel.Directive(id=directive_id, feature_id=fid, kind="steer", text="x")])
    run_loop_b(str(_root), str(codoc_dir))

    assert store.comments_for_feature(fid)[0].status is CommentStatus.RESOLVED


def test_an_unrelated_landing_leaves_the_thread_sent(repo):
    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="do the thing", comment_id="cm-1", body="do the thing"))
    run_loop_b(str(_root), str(codoc_dir))

    edits_channel.log_realized(codoc_dir, [
        edits_channel.Directive(id="d-somebody-else", feature_id=fid, kind="amend", text="x")])
    run_loop_b(str(_root), str(codoc_dir))

    assert store.comments_for_feature(fid)[0].status is CommentStatus.SENT


def test_threads_reach_the_sidecar(repo):
    """The IDE reads them from derived state now, not from its own memory."""
    from codoc.codoc_file.render import write_sidecar
    from codoc.loop.fsio import read_json

    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="do the thing", comment_id="cm-1", body="do the thing",
        anchor_text="Accepts files.", code_refs=["upload.py::handle"], scope="both"))
    run_loop_b(str(_root), str(codoc_dir))
    write_sidecar(store, codoc_dir)

    sidecar = read_json(codoc_dir / "tree.bindings.json", default={})
    rows = sidecar["comments"][fid]
    assert rows[0]["id"] == "cm-1"
    assert rows[0]["body"] == "do the thing"
    assert rows[0]["anchor_text"] == "Accepts files."
    assert rows[0]["code_refs"] == ["upload.py::handle"]
    assert rows[0]["scope"] == "both"
    assert rows[0]["directive_id"].startswith("d-")


def test_a_code_scoped_thread_omits_the_default_from_the_sidecar(repo):
    """Presence-keyed: a thread that carries nothing unusual costs nothing to ship."""
    from codoc.codoc_file.render import write_sidecar
    from codoc.loop.fsio import read_json

    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="note", comment_id="cm-1", body="note"))
    run_loop_b(str(_root), str(codoc_dir))
    write_sidecar(store, codoc_dir)

    row = read_json(codoc_dir / "tree.bindings.json", default={})["comments"][fid][0]
    assert "scope" not in row
    assert "code_refs" not in row


def test_the_host_op_carries_the_whole_thread(repo):
    """`edits.host.jsonl` is the only thing the IDE writes; a field it drops here is a
    field the daemon never sees."""
    _root, codoc_dir, _store = repo
    edits_channel.append_host_op(codoc_dir, "appendSteer", {
        "feature_id": "f-1", "text": "note", "comment_id": "cm-1", "body": "note",
        "anchor_text": "the sentence", "code_refs": ["a.py::b"], "scope": "both"})
    edits_channel.merge_host_ops(codoc_dir)

    [s] = edits_channel.read_steers(codoc_dir)
    assert (s.body, s.anchor_text, s.code_refs, s.scope) == ("note", "the sentence", ["a.py::b"], "both")


def test_the_resolve_host_op_routes(repo):
    _root, codoc_dir, _store = repo
    edits_channel.append_host_op(codoc_dir, "resolveComment", {"comment_id": "cm-9"})
    edits_channel.merge_host_ops(codoc_dir)
    assert edits_channel.read_comment_resolves(codoc_dir) == ["cm-9"]


def test_a_comment_less_steer_does_not_misattribute_its_neighbour(repo):
    """The pairing is by identity, not position.

    `steered` takes EVERY drained steer while only the ones carrying a thread id are
    tracked for stamping, so one comment-less steer (the CLI path) used to shift the
    alignment and stamp a thread with somebody else's directive.
    """
    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="the note with a thread", comment_id="cm-1",
        body="the note with a thread"))
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="a bare CLI steer"))          # no comment_id

    run_loop_b(str(_root), str(codoc_dir))

    thread = store.comments_for_feature(fid)[0]
    directives = {d.id: d for d in edits_channel.peek_manifest(codoc_dir)}
    assert len(directives) == 2
    assert directives[thread.directive_id].caused_by == "cm-1"
    assert "the note with a thread" in directives[thread.directive_id].text


def test_a_resolved_thread_leaves_the_margin_but_not_the_record(repo):
    """"Resolve" used to be a button that could not do its job: the thread stayed in the
    sidecar forever, so every projection brought the card back. It leaves the page after
    a while — and stays in the store, which is where the durable answer to "why does this
    code look like this" belongs."""
    from codoc.model.annotation import RESOLVED_LINGER_S, in_margin
    from codoc.model.hlc import HLC

    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="note", comment_id="cm-1", body="note"))
    run_loop_b(str(_root), str(codoc_dir))
    edits_channel.append_comment_resolve(codoc_dir, "cm-1")
    run_loop_b(str(_root), str(codoc_dir))

    thread = store.comments_for_feature(fid)[0]
    assert thread.status is CommentStatus.RESOLVED
    now = HLC.now().wall_clock
    # It lingers long enough for the author to read what their request produced …
    assert in_margin(thread, now)
    # … and then it is gone from the page, while the record itself is untouched.
    assert not in_margin(thread, now + int(RESOLVED_LINGER_S * 1000) + 1000)


def test_an_aged_out_thread_stops_anchoring_the_prose(repo):
    """Otherwise a dotted underline points at a card nothing renders — an annotation on
    the prose with nothing behind it."""
    from codoc.codoc_file.doc_render import build_doc_from_store
    from codoc.model.annotation import CommentStatus as CS
    from codoc.model.annotation import CommentThread
    from codoc.model.hlc import HLC

    _root, _cd, store = repo
    fid = _feature(store)
    old = CommentThread(id="cm-old", feature_id=fid, body="ancient", status=CS.RESOLVED,
                        anchor_start=0, anchor_end=7)
    old.updated_at = HLC(wall_clock=HLC.now().wall_clock - 10_000_000, logical_time=0,
                         node_id="n")
    store.upsert_comment(old)

    doc = build_doc_from_store(store)
    marks = [m for block in doc["content"] for run in (block.get("content") or [])
             for m in (run.get("marks") or [])]
    assert not any(m.get("type") == "comment" for m in marks)


def test_a_thread_remembers_which_words_it_was_about(repo):
    """Offsets are what survive a reload — the webview's ProseMirror range does not. A
    thread persisted without them re-anchored at character zero, which is the
    invisible-anchor problem coming back the moment the tab reopens."""
    _root, codoc_dir, store = repo
    fid = _feature(store, description="Accepts files. Retries three times.")
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="note", comment_id="cm-1", body="note",
        anchor_text="Retries three times."))
    run_loop_b(str(_root), str(codoc_dir))

    t = store.comments_for_feature(fid)[0]
    assert t.anchor_start == len("Accepts files. ")
    assert t.anchor_end == len("Accepts files. Retries three times.")


def test_an_anchor_whose_words_are_gone_claims_no_span(repo):
    """Better to fall back to the feature than to silently point at character zero."""
    _root, codoc_dir, store = repo
    fid = _feature(store, description="Accepts files.")
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="note", comment_id="cm-1", body="note",
        anchor_text="a sentence that is no longer here"))
    run_loop_b(str(_root), str(codoc_dir))

    t = store.comments_for_feature(fid)[0]
    assert (t.anchor_start, t.anchor_end) == (0, 0)
    assert t.anchor_text == "a sentence that is no longer here"   # still the locator


def test_an_anchor_inside_bolded_prose_still_lands(repo):
    """Bold serializes as `**…**` in the description, and anchor offsets are measured in
    that marker-INCLUSIVE space (doc_render keeps the markers occupying their positions so
    annotations stay aligned). The webview's anchor text carries no markers, so this only
    works because the quoted words appear verbatim between them — pinned here because the
    two facts live in different files and a change to either would break it silently."""
    _root, codoc_dir, store = repo
    fid = _feature(store, description="Accepts files, **at most 5 per minute**, per host.")
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="note", comment_id="cm-1", body="note",
        anchor_text="at most 5 per minute"))
    run_loop_b(str(_root), str(codoc_dir))

    t = store.comments_for_feature(fid)[0]
    described = store.get_feature(fid).description
    assert described[t.anchor_start:t.anchor_end] == "at most 5 per minute"


def test_an_anchor_straddling_a_bold_boundary_declines_rather_than_guessing(repo):
    """`files, at most 5` reads as `files, **at most 5` in the stored text, so the quoted
    words are not there to find. Falling back to the feature is right; landing the span at
    a plausible-looking wrong offset would be worse than carrying none."""
    _root, codoc_dir, store = repo
    fid = _feature(store, description="Accepts files, **at most 5 per minute**.")
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="note", comment_id="cm-1", body="note",
        anchor_text="files, at most 5"))
    run_loop_b(str(_root), str(codoc_dir))

    t = store.comments_for_feature(fid)[0]
    assert (t.anchor_start, t.anchor_end) == (0, 0)
    assert t.anchor_text == "files, at most 5"


def test_a_landed_thread_gets_an_answer_naming_the_code(repo):
    """A comment that only ever changed colour left the author to find out elsewhere
    whether their note had been acted on. The reply is built from the ledger — the ops
    that cite the directive, and the files they bound — so it is a claim codoc can stand
    behind rather than a second telling of work nobody recorded."""
    from codoc.model.binding import Binding

    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="cap it", comment_id="cm-1", body="cap it"))
    run_loop_b(str(_root), str(codoc_dir))
    did = store.comments_for_feature(fid)[0].directive_id

    # The agent implements and reflects, citing the directive.
    apply_op(NodeOp(kind=NodeOpKind.ATTACH, feature_id=fid,
                    bindings=[("upload.py", "upload.py::handle")]),
             store, source="loop_a_agent", applied=True, actor="claude-code",
             mode="auto", caused_by=did)
    edits_channel.log_realized(codoc_dir, [
        edits_channel.Directive(id=did, feature_id=fid, kind="steer", text="x")])
    run_loop_b(str(_root), str(codoc_dir))

    t = store.comments_for_feature(fid)[0]
    assert t.status is CommentStatus.RESOLVED
    assert len(t.replies) == 1
    assert t.replies[0].author == "claude-code"
    assert "upload.py" in t.replies[0].body


def test_a_reply_does_not_claim_files_that_were_never_recorded(repo):
    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="note", comment_id="cm-1", body="note"))
    run_loop_b(str(_root), str(codoc_dir))
    did = store.comments_for_feature(fid)[0].directive_id
    edits_channel.log_realized(codoc_dir, [
        edits_channel.Directive(id=did, feature_id=fid, kind="steer", text="x")])
    run_loop_b(str(_root), str(codoc_dir))

    [reply] = store.comments_for_feature(fid)[0].replies
    assert "no code was bound" in reply.body


def test_replies_survive_a_re_sent_steer(repo):
    """Editing the note re-sends the steer, which carries no replies — blanking them
    would erase the answers the thread already has."""
    from codoc.model.annotation import CommentReply

    _root, codoc_dir, store = repo
    fid = _feature(store)
    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="v1", comment_id="cm-1", body="v1"))
    run_loop_b(str(_root), str(codoc_dir))
    t = store.comments_for_feature(fid)[0]
    t.replies = [CommentReply(author="claude-code", body="Done — changed a.py.")]
    store.upsert_comment(t)

    edits_channel.append_steer(codoc_dir, edits_channel.Steer(
        feature_id=fid, text="v2", comment_id="cm-1", body="v2"))
    run_loop_b(str(_root), str(codoc_dir))

    again = store.comments_for_feature(fid)[0]
    assert again.body == "v2"
    assert [r.body for r in again.replies] == ["Done — changed a.py."]
