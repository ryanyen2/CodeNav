"""Derived views and the Markdown export fixed point (spec §6, §9)."""
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import (
    attribution, description, directive, ground, history, ink, markdown, phase, plan_claims, projection,
)
from codoc.calculus import block_md
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.ingest import block_bodies, resolve_pending_refs
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, SUGGEST_DEL

from tests.calculus.test_apply import Mint, heading, paragraph


def import_markdown(md: str) -> list[dict]:
    """Op bodies that build the document ``md`` describes, keeping the ids it carries."""
    return block_bodies(block_md.parse(md), after=START, keep_ids=True)[0]


def build(store: Store, m: Mint, bodies: list[dict], actor="import"):
    """Apply a batch of bodies the way ``Workspace.submit`` does, resolving ``$ins`` anchors."""
    insert_ids: list[str] = []
    for b in bodies:
        payload = dict(b["payload"])
        resolve_pending_refs(payload, insert_ids)
        op = apply(store, m.op(b["kind"], b["target"], payload, actor=actor))
        assert op.conflict is None, (b, op.conflict)
        if b["kind"] == K.TEXT_INSERT:
            insert_ids.append(op.id)


SAMPLE = """# Networking {#b-000000000001}

The client **retries three times** via [send](codoc:net/client.py#Client.send), see [rfc](https://r.fc/1).

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
{"url": "https://example.org/spec"}
```

# Storage {#b-000000000004}
"""


def test_export_import_export_is_a_fixed_point():
    import codoc.kinds  # noqa: F401  (the shipped kinds register themselves)
    m, s = Mint(), Store()
    build(s, m, import_markdown(SAMPLE))
    out = export_markdown(s)
    assert out == SAMPLE
    m2, s2 = Mint("t"), Store()
    build(s2, m2, import_markdown(out))
    assert export_markdown(s2) == SAMPLE
    assert description(s, "b-000000000001").startswith("The client **retries three times**")
    assert description(s, "b-000000000001").count("\n\n") == 1


def test_three_channels_and_phase():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client", actor="loop")                              # bootstrap prose is not ink
    ins = paragraph(m, s, "p1", "Retries once.", after="h1", actor="loop")
    assert phase(s, "h1") == "synced"
    # Human types: unsent ink, phase edited.
    h = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 12], "text": " Then stops."}))
    claims = ink(s, "h1")
    assert [(c.stage, c.text) for c in claims] == [("unsent", " Then stops.")]
    assert phase(s, "h1") == "edited"
    # Hand-off: sent, phase planned (no bindings) then queued once bound.
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "stop after one retry"}))
    assert [c.stage for c in ink(s, "h1")] == ["sent"]
    assert phase(s, "h1") == "planned"
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "Client"}))
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
    """02 §2: "empty a title by deleting characters → a feature with an empty
    title, flagged". `ink` is what the person WROTE — 09-editor §7 draws
    removals not at all, because they are the one who removed them — so a
    delete-only edit left the phase `synced`, the status bar silent and the
    hand-off unoffered, while the directive was already carrying a `removed:`
    line for it."""
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
