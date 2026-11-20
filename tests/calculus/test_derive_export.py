"""Derived views and the Markdown export fixed point."""
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import (
    attribution,
    description,
    ground,
    history,
    ink,
    markdown,
    phase,
    plan_claims,
    projection,
)
from codoc.calculus.block_md import GENERATED
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.derive import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.inline_md import Span, render
from codoc.calculus.ops import covered
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK
from codoc.loops.realize import directive
from codoc.workspace import Workspace
from tests.helpers import Mint, build, heading, import_markdown, paragraph, seed_loop

SAMPLE = GENERATED + """

# Networking {#b-000000000001}

The client **retries three times** via [send](codoc:net/client.py::Client.send), see [rfc](https://r.fc/1).

Second paragraph.

## Backoff {#b-000000000002}

Exponential, capped.

```mermaid
graph TD; A-->B
```

- one **point**
    1. a step under it

| a | b |
|---|---|
| 1 | 2 |

```codoc:url {#b-000000000003}
{"$layout": {"export": true, "flow": true, "text": ["caption"]}, "url": "https://example.org/spec"}
```

# Storage {#b-000000000004}
"""


def test_export_import_export_is_a_fixed_point():
    import codoc.kinds  # noqa: F401  (the shipped kinds register themselves)
    m, s = Mint(), Store()
    build(s, m, import_markdown(SAMPLE))
    out = export_markdown(s)
    assert out == SAMPLE and out.splitlines()[0] == GENERATED     # says it is derived; parse skips it
    m2, s2 = Mint("t"), Store()
    build(s2, m2, import_markdown(out))
    assert export_markdown(s2) == SAMPLE
    assert description(s, "b-000000000001").startswith("The client **retries three times**")
    assert description(s, "b-000000000001").count("\n\n") == 1


def test_the_projection_says_who_edited_each_feature_since_its_hand_off():
    """⌘S hands off only the features naming the viewer, read off the actors of
    the unsent words and removals."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    a = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    heading(m, s, "h2", "Server", after="p1", actor="loop")
    b = paragraph(m, s, "p2", "Listens.", after="h2", actor="loop")
    assert projection(s)["editors"] == {}
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [a.id, 12], "text": " Mine."}, actor="human:a"))
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": START, "text": "Then "}, actor="human:b"))
    apply(s, m.op(K.TEXT_DELETE, {"bid": "p2", "path": "text"}, {"from": [b.id, 0], "to": [b.id, 2]}, actor="human:b"))
    assert projection(s)["editors"] == {"h1": ["human:a", "human:b"], "h2": ["human:b"]}
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}, actor="human:a"))
    assert projection(s)["editors"] == {"h2": ["human:b"]}


def test_three_channels_and_phase():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")                              # bootstrap prose is not ink
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    assert phase(s, "h1") == "synced"
    # Human types: unsent ink, phase edited.
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " Then stops."}))
    claims = ink(s, "h1")
    assert [(c.stage, c.text) for c in claims] == [("unsent", " Then stops.")]
    assert phase(s, "h1") == "edited"
    # Hand-off: sent, phase planned (no bindings) then queued once bound.
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "stop after one retry"}))
    assert [c.stage for c in ink(s, "h1")] == ["sent"]
    assert phase(s, "h1") == "planned"
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"symbol": "Client"}))
    assert phase(s, "h1") == "queued"
    d = directive(s, "h1")
    assert "Retries once. Then stops." in d and "Author asked: stop after one retry" in d
    assert 'added: " Then stops."' in d
    # Realized: synced. Then the loop reflects a repair: ground.
    apply(s, m.op(K.FEATURE_REALIZED, {"fid": "h1"}, {"sha": "abc"}))
    assert phase(s, "h1") == "synced"
    loop = Mint("l", "loop")
    t = s.blocks["p1"].text()
    apply(s, loop.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 8], "to": [ins.id, 11]}))
    apply(s, loop.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 11], "text": "twice"}))
    g = ground(s, "h1")
    assert [(c.stage, c.text) for c in g] == [("removed", "once"), ("added", "twice")]
    assert ink(s, "h1") == []          # the human's words are realized; no ink
    # An agent plan: opacity.
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit", "note": "n"}))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(t.last_live_ref()), "text": " Maybe."}, plan="pl"))
    assert [(c.stage, c.text) for c in plan_claims(s, "h1")] == [("open", " Maybe.")]
    assert phase(s, "h1") == "proposed"
    # Directive renders the all-rejected mode: the plan's words are absent.
    assert "Maybe" not in directive(s, "h1")
    assert markdown(s.blocks["p1"].text(), "rejected", s).endswith("Then stops.")
    assert markdown(s.blocks["p1"].text(), "live", s).endswith("Maybe.")
    # Attribution runs merge by actor.
    runs = attribution(s.blocks["p1"].text())
    assert [r.actor for r in runs] == ["loop", "loop", "loop", "human:a", "agent:x"] or \
        [r.actor for r in runs] == ["loop", "human:a", "agent:x"]
    # History lists ops on this feature's blocks.
    assert all(o.target.get("bid") in ("h1", "p1") or o.target.get("fid") == "h1" for o in history(s, "h1"))
    p = projection(s)
    assert p["phases"] == {"h1": "proposed"} and p["blocks"][1]["fields"]["text"]["text"].endswith("Maybe.")
    # Accepted: the plan's words are ordinary text, still the agent's.
    apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {}))
    assert plan_claims(s, "h1") == [] and attribution(s.blocks["p1"].text())[-1].actor == "agent:x"


def test_focus_from_human_bold_only():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    ins = paragraph(m, s, "p1", "Retries once and logs.", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    loop = Mint("l", "loop")
    apply(s, loop.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 17], "end": [ins.id, 20], "on": True}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    d = directive(s, "h1")
    assert 'Focus: "once"' in d and "logs" not in d.split("Focus:")[1].splitlines()[0]


def test_a_person_who_only_deleted_words_has_something_to_hand_off(tmp_path):
    """`ink` holds only what a person wrote, but a person's kill outside both
    watermarks also makes the phase `edited`. A delete-only edit therefore
    shows in the phase, and so in the status bar and the hand-off, as it does
    in the directive's `removed:` line."""
    m, s = Mint(), Store()
    loop = Mint("l", "loop")
    heading(loop, s, "h1", "Client")
    ins = paragraph(loop, s, "p1", "Retries once.", after="h1")
    assert phase(s, "h1") == "synced"
    apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                  {"from": [ins.id, 8], "to": [ins.id, 11]}))
    assert phase(s, "h1") == "edited"
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "drop the retry"}))
    assert phase(s, "h1") in ("queued", "planned")
    assert 'removed: "once"' in directive(s, "h1")


def test_a_verdicts_removal_is_nobodys_unsent_edit(tmp_path):
    """Accepting a plan's strike or rejecting its words kills characters as
    the person who answered, but that answers the plan: the feature stays
    synced, before and after a fold, and a later hand-off asks no removal."""
    from tests.helpers import fold
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    for verdict, words in ((K.PLAN_ACCEPT, "Retries twice."), (K.PLAN_REJECT, "Retries three times.")):
        pid = ws.amend("h1", rewrites=[("p1", "text", words, None)], actor="loop", structural=True).plan
        ws.submit([{"kind": verdict, "target": {"pid": pid}, "payload": {}}], actor="human:local")
        assert phase(ws.store, "h1") == "synced", verdict
    assert description(ws.store, "h1") == "Retries twice."
    ws.close()
    fold(cd)
    ws = Workspace(cd)
    assert phase(ws.store, "h1") == "synced"
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="human:local")
    assert "removed:" not in directive(ws.store, "h1")


def test_a_suggesters_strike_the_owner_accepted_is_the_owners_request(tmp_path):
    """Accepting a person's plan adopts it: its strike is pending intent, with
    the one who accepted as its editor. Rejecting a person's plan takes back
    only its own words, which the document never held."""
    from codoc.calculus.derive import editors
    from codoc.workspace import inbox as I
    from tests.helpers import H, refs, sub, write_roles
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed_loop(ws); ws.close()
    write_roles(cd, {"human:gh:bob": "suggest"})
    ws = Workspace(cd)
    r = refs(ws, "p1")                                   # "Retries once before giving up."

    def bob(kind, payload):
        I.submit(cd, "hub-bob", [sub(kind, kind, {"bid": "p1", "path": "text"}, payload)])
        pid = ws.drain_inbox()[-1].plan
        assert ws.store.plans[pid].actor == "human:gh:bob"
        return pid

    for kind, payload in ((K.TEXT_INSERT, {"after": list(r[-1]), "text": " Politely."}),
                          (K.TEXT_DELETE, {"from": list(r[7]), "to": list(r[11])})):
        pid = bob(kind, payload)
        verdict = K.PLAN_REJECT if kind == K.TEXT_INSERT else K.PLAN_ACCEPT
        ws.submit([{"kind": verdict, "target": {"pid": pid}, "payload": {}}], actor=H)
    assert description(ws.store, "h1") == "Retries before giving up."
    assert phase(ws.store, "h1") == "edited" and editors(ws.store, "h1") == [H]
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    assert directive(ws.store, "h1").count("removed:") == 1 and 'removed: " once"' in directive(ws.store, "h1")
    ws.close()


def _suggester_types_then_takes_back(ws, cd, text, back):
    """A hub suggester types ``text`` at the end of p1, which lands as their
    plan, then deletes characters ``back`` (k0, n) of it: their own proposed
    words, so the delete rides in the same plan. Returns the plan."""
    from codoc.workspace import inbox as I
    from tests.helpers import refs, sub
    I.submit(cd, "hub-mallory", [sub("i", K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                                     {"after": list(refs(ws, "p1")[-1]), "text": text})])
    ins = ws.drain_inbox()[0]
    assert ins.conflict is None and ins.plan
    I.submit(cd, "hub-mallory", [sub("d", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                     {"segments": [[ins.id, *back]]}, batch="b2")])
    (gone,) = ws.drain_inbox()
    assert gone.conflict is None and gone.plan == ins.plan
    return ins.plan


def test_a_suggester_taking_back_their_own_proposed_words_removes_nothing(tmp_path):
    """Deleting a word your own open plan put there is the plan getting
    smaller (docs/concurrency.md section 9), not a person's removal: the
    owner's feature has no editor and is not `edited`, which would hold it
    from the loop, turn machine writes into plans and keep ⌘S from handing it
    off."""
    from codoc.calculus.derive import editors, removals
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    _suggester_types_then_takes_back(ws, cd, " Maybe", (0, 6))
    assert removals(ws.store, "h1") == [] and editors(ws.store, "h1") == []
    assert phase(ws.store, "h1") == "proposed"          # the plan stands, empty, for a verdict
    ws.close()


def test_a_suggesters_own_correction_is_no_removal_once_accepted(tmp_path):
    """Accepted, the plan's taken-back words are still the plan's own: the
    directive has no `removed:` line for them, only the words it adds."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    pid = _suggester_types_then_takes_back(ws, cd, " Maybe, or drop the retry loop", (6, 24))
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="human:local")
    d = directive(ws.store, "h1")
    assert "removed:" not in d and "Retries once before giving up. Maybe" in d, d
    ws.close()


def test_ink_keeps_unsent_words_apart_from_sent_neighbours():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    a = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " A"}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"symbol": "c.py::C"}))
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [a.id, 1], "text": " B"}))
    assert [(c.stage, c.text) for c in ink(s, "h1")] == [("sent", " A"), ("unsent", " B")]
    assert phase(s, "h1") == "edited"
    assert phase(s, "h1", realizing={"h1"}) == "edited"          # the document wins over the lease
    d = directive(s, "h1")
    assert 'added: " A"' in d and '" A B"' not in d


def test_partial_realization_is_one_fact():
    a, b, s = Mint("a", "human:a"), Mint("b", "human:a"), Store()
    heading(a, s, "h1", "C", actor="loop")
    ins = paragraph(a, s, "p1", "x", after="h1", actor="loop")
    apply(s, a.op(K.BIND_ATTACH, {"fid": "h1"}, {"symbol": "c.py::C"}))
    apply(s, b.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 0], "text": "y"}))
    apply(s, a.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    w = s.watermarks("h1")
    apply(s, a.op(K.FEATURE_REALIZED, {"fid": "h1"}, {"covers": {"a": 5}}))
    assert not covered(w.handoff, w.realized) and phase(s, "h1") == "queued"
    assert [c.stage for c in ink(s, "h1")] == ["sent"]


def test_bold_across_a_cite_edge_and_literal_markup_round_trip():
    assert render("abc", [Span(LINK, {"href": "https://u"}, 0, 2), Span(BOLD, {}, 1, 3)]) == "[a**b**](https://u)**c**"
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "T")
    ins = paragraph(m, s, "b-000000000002", "see the client now", after="b-000000000001")
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"},
                  {"kind": CITE, "attrs": {"symbol": "c.py::C"}, "start": [ins.id, 4], "end": [ins.id, 13], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "b-000000000002", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 13], "on": True}))
    paragraph(m, s, "b-000000000003", "# Not a heading {#b-000000000009}", after="b-000000000002")
    paragraph(m, s, "b-000000000004", "type **stars** to bold and [x](https://y) too", after="b-000000000003")
    md1 = export_markdown(s)
    s2 = Store()
    build(s2, Mint("t"), import_markdown(md1))
    assert export_markdown(s2) == md1
    paras = [b for b in s2.ordered() if b.kind == PARAGRAPH]
    assert [p.text().live_text() for p in paras] == ["see the client now", "# Not a heading {#b-000000000009}",
                                                      "type **stars** to bold and [x](https://y) too"]
    assert sorted(mk.kind for mk in paras[0].text().marks) == [BOLD, CITE] and paras[1].text().marks == []
    assert len([b for b in s2.ordered() if b.kind == HEADING]) == 1


def test_history_follows_an_op_to_the_block_it_landed_in_and_lists_conflicts():
    m, s = Mint(), Store()
    heading(m, s, "h1", "One")
    paragraph(m, s, "a", "aaa", after="h1")
    heading(m, s, "h2", "Two", after="a")
    b = paragraph(m, s, "b", "bbb", after="h2")
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "b"}, {"after": "a"}))
    apply(s, m.op(K.BLOCK_MERGE, {"into": "a", "bid": "b"}, {}))
    ins = apply(s, m.op(K.TEXT_INSERT, {"bid": "b", "path": "text"}, {"after": [b.id, 2], "text": "!"}))
    bad = apply(s, m.op(K.TEXT_INSERT, {"bid": "a", "path": "text"}, {"after": ["nope:1", 0], "text": "?"}))
    assert ins.payload["bid"] == "a" and bad.conflict == "unknown_anchor"
    h = history(s, "h1")
    assert ins in h and bad in h and ins not in history(s, "h2")


def test_ground_paints_nothing_on_a_tree_nobody_touched_and_shows_repairs_after():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    assert ground(s, "h1") == []
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " Mine."}))
    loop = Mint("l", "loop")
    apply(s, loop.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 8], "to": [ins.id, 11]}))
    apply(s, loop.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 11], "text": "twice"}))
    assert [(c.stage, c.text) for c in ground(s, "h1")] == [("removed", "once"), ("added", "twice")]


def test_verdicts_are_in_the_features_history(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "P "}}], actor="loop", plan=pid)
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="agent:x")[0]
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0]
    h = history(ws.store, "h1")
    assert bad.conflict == "authority" and bad in h and acc in h


def test_a_fold_changes_neither_who_owns_a_comment_nor_what_lower_compares_against(tmp_path):
    """Both are read from the blocks, not from the ops a snapshot archives: a
    suggester still owns the comment they added (the fold's `block.add` is the
    importer's), and a person's set since the last realization still shows its
    old value."""
    import codoc.kinds  # noqa: F401  (registers the comment and url layouts)
    from codoc.calculus.derive import realized_content
    from codoc.calculus.ops import Op
    from tests.helpers import H, fold, kind_block, seed
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    alice = "human:gh:alice"
    c = kind_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "once", "body": "Twice?"},
                   after="p1", actor=alice)
    u = kind_block(ws, "url", {"url": "https://a.example"}, after="p1")
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    ws.submit([{"kind": K.BLOCK_SET, "target": {"bid": u, "path": "url"}, "payload": {"value": "https://b.example"}}],
              actor=H)

    def answers(store):
        write = lambda actor: Op(id="x:1", at="", actor=actor, batch="", kind=K.TEXT_INSERT,
                                 target={"bid": c, "path": "body"}, payload={"after": START, "text": "Or"})
        old = realized_content(store, store.blocks[u], store.watermarks("h1").realized)
        return ws.roles.policy(store, write(alice)), bool(ws.roles.policy(store, write("human:gh:bob"))), old["url"]

    before = answers(ws.store)
    assert before == (None, True, "https://a.example")
    ws.close()
    fold(cd)
    assert answers(Workspace(cd, readonly=True).store) == before


def test_in_flight_lists_a_queued_feature_and_one_an_offered_plan_targets_whatever_their_phase(tmp_path):
    """`edited` outranks `queued` and `proposed` in the phase; `in_flight`
    still lists both kinds of work, so the editor keeps those headings."""
    from codoc.calculus.derive import in_flight
    from tests.helpers import H, seed
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "B"}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "h2"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "b"}}],
              actor=H)
    assert in_flight(ws.store) == []
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.open_plan(actor="agent:claude-code", target="p2")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(ws.store.blocks["p1"].text().last_live_ref()), "text": " More."}}], actor=H)
    assert phase(ws.store, "h1") == "edited" and phase(ws.store, "h2") == "edited"
    assert in_flight(ws.store) == ["h1", "h2"]


def test_the_loop_reads_a_plan_answering_a_hand_off_as_taken_but_not_a_build_plan(tmp_path):
    """An agent's reflect of code it built for hand-off d, still open, is code
    the loop must not describe again. A build plan citing d comes before the
    code, so the loop leaves its words out."""
    from tests.helpers import H, seed_loop
    ws = Workspace(tmp_path / ".codoc"); seed_loop(ws)
    d = ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)[0]
    agent = "agent:claude-code"
    end = lambda: list(ws.store.blocks["p1"].text().last_live_ref())   # noqa: E731
    built = ws.open_plan(actor=agent, target="p1")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": end(), "text": " Built."}}],
              actor=agent, plan=built, caused_by=d.id)
    ws.submit([{"kind": K.PLAN_OPEN, "target": {"pid": "p-build"}, "payload": {"target": "p1", "build": True}}],
              actor=agent, batch="p-build")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": end(), "text": " Wanted."}}],
              actor=agent, plan="p-build", caused_by=d.id)
    assert markdown(ws.store.blocks["p1"].text(), "loop", ws.store) == "Retries once before giving up. Built."
