# Review of the calculus kernel, implementation 1

Scope: `codoc/calculus/{ids,text,ops,doc,store,apply,rank}.py` and `tests/calculus/*` on
branch `substrate` (worktree `CodeNav-substrate`), read against `docs/substrate/01-calculus.md`
rev 2 and `07-design-review-1.md`. The 21 shipped tests pass. Every failing sequence in §A was
run against the code and confirmed; the probe file is
`scratchpad/probe_kernel.py` next to this review (21 probes, all confirming). Timings in §C
were measured.

Verdict up front. The shape is right: one sequence of live and dead segments per field,
identity everywhere, a handler per op returning `(canonical, inverse)`, conflicts recorded
rather than raised. Three things are not yet true that the spec promises, and each is a
class rather than a case: (1) after `block.merge` the same character identities are held by
two blocks, so the "look the character up" rule in §3 resolves to the dead one; (2) every
place that re-finds a plan's or a delete's characters uses a *recorded block id* or a
*sequence range* instead of the character identities, so a split, merge or later insert
between the op and its verdict or undo silently kills the wrong text or none; (3) a handler
that conflicts halfway leaves its partial mutation in the store with `inverse=[]`, so the
ledger says nothing happened while the cache changed, and replay faithfully repeats the
lie. Everything else is a normal first-pass list.

---

## A. Correctness bugs

Each item has a pasteable test using the helpers in `tests/calculus/test_apply.py`. The
assertions state the *observed* wrong behaviour so the test passes today; flip the marked
line to make it the regression test. `T(s, bid)` is
`s.blocks[bid].text().live_text()`.

### A1. `block.merge` leaves the dead block holding the survivor's characters; identity lookup then returns the tombstoned block

`_h_block_merge` does `ta.merge(tb)` (or `tb.clone().merge(ta)`), which *extends* the
survivor's list with the same `Seg` objects, and never clears `b.content[path]`. The dead
block's `Text` still `holds()` every one of those refs. `Store.locate_char` tries the hint
first and then `sorted(char_index[op])`; the hint is the client's last-known block (the
dead one, after a merge it has not seen), and even without a hint the sort by block id
picks whichever id is lexically smaller. `_h_text_insert` then raises `dead_block`. This is
the exact case §3 says "costs nothing".

```python
def test_merge_dead_block_still_holds_chars_and_hijacks_locate():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    a = paragraph(m, s, "a", "first", after="h1")
    b = paragraph(m, s, "b", "second", after="a")
    mg = apply(s, m.op(K.BLOCK_MERGE, {"into": "b", "bid": "a"}, {}))   # survivor sorts AFTER the dead one
    assert mg.conflict is None and T(s, "b") == "firstsecond"
    assert s.blocks["a"].text().holds((a.id, 0))                          # dead block still holds the identity
    ins = apply(s, m.op(K.TEXT_INSERT, {"bid": "a", "path": "text"}, {"after": [a.id, 4], "text": "!"}))
    assert ins.conflict == "dead_block"          # BUG: should land in b
    ins2 = apply(s, m.op(K.TEXT_INSERT, {"bid": "zzz", "path": "text"}, {"after": [a.id, 4], "text": "!"}))
    assert ins2.conflict == "dead_block"         # BUG: no hint, index sorted by bid, "a" < "b"
```

The same duplication makes A2's merge case kill the *dead* copy: `text.delete` replaces
`Seg` objects in the dead block's list, the survivor's list keeps the live ones, and the
op reports `killed` non-empty.

Fix: a character identity lives in exactly one `Text`. On merge, set
`b.content[path] = Text()` (segments and marks both moved; the inverse `block.split` with
`new_takes` re-populates it from the survivor, so nothing is lost), and drop `(b, path)`
from `char_index` for the moved ops or let `holds()` be the filter as now. Also make
`locate_char` prefer a *live* holder over a dead one when both hold (cheap: two passes).

### A2. Plan verdicts and their undo re-find characters by recorded block id, not identity

`_kill_plan_inserts` reads `o.payload["bid"]` (the block at insert time) and scans that
block's segments; `_h_plan_reopen` does `store.blocks[k["bid"]]` for `killed` and `marks`.
After a human `block.split` moved the plan's segment into the new half, or a `block.merge`
moved it into the survivor (A1), the scan finds nothing and the op reports success with
`killed == []`. The rejected suggestion stays live, plan-tagged, on the trunk, forever.
This is the "prints success" class from the demo notes.

```python
def test_reject_misses_plan_insert_moved_by_split():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 9], "text": " gamma"}, plan="pl"))
    apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 4], "new": "p2"}))
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert rj.conflict is None
    assert T(s, "p2") == " beta gamma" and rj.payload["killed"] == []     # BUG: " gamma" must be dead

def test_reject_misses_plan_insert_in_merged_block():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    paragraph(m, s, "p1", "one", after="h1")
    ins2 = paragraph(m, s, "p2", "two", after="p1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p2", "path": "text"}, {"after": [ins2.id, 2], "text": " more"}, plan="pl"))
    apply(s, m.op(K.BLOCK_MERGE, {"into": "p1", "bid": "p2"}, {}))
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert rj.conflict is None
    assert T(s, "p1") == "onetwo more" and rj.payload["killed"]          # BUG: killed the dead copy, reports success
```

Fix: `store.char_index[o.id]` is exactly the index for this. For each plan insert, walk
`char_index[o.id]`, and in each holding `Text` kill the live segments with `s.op == o.id`.
Same in `_h_plan_reopen`: route `killed`/`marks` restores through `store.locate_char`,
as `_h_text_restore` already does.

### A3. `text.delete` by `segments` kills a sequence *range*, not the named segments

The canonical form of a delete is `[(op, k0, len)]`, and the handler applies it as
`text.delete((op,k0), (op,k0+len-1))`, which walks the sequence between those two
positions and kills every live character in between, whoever minted it. At replay time
that is harmless (nothing else is there yet). As the inverse of `text.restore`, i.e. a
redo, or as any later re-application, it kills characters typed inside the range since.
`Text.restore` already filters by `s.op == op`; `delete` does not.

```python
def test_delete_by_segments_kills_chars_minted_later_inside_the_range():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "hello", after="h1")
    d = apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 0], "to": [ins.id, 4]}))
    (r,) = invert(s, m, d)                                        # restore -> "hello"
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 2], "text": "X"}))
    redo = invert(s, m, r)                                        # inverse of restore = delete segments [(ins,0,5)]
    assert redo[0].conflict is None
    assert T(s, "p1") == ""                                       # BUG: expected "X"
```

Fix: give `Text.delete` an `only: OpId | None` filter (kill a segment in the walk only if
`s.op == only`), and pass it from the `segments` path. Then delete and restore are
symmetric.

### A4. A conflict raised after the first mutation leaves a partial state with `inverse=[]`

`apply` wraps the handler in one try; handlers mutate as they go. Any handler that loops
over several anchors can fail on the second: `_h_text_delete` (segments), `_h_text_restore`,
`_h_block_move` (restore path, `store.blocks[bid]` KeyError), `_h_plan_accept` (the
`_suggest_marks` loop after `_kill_plan_inserts` has already run), `_h_plan_reject`
(`store.blocks[bid]` in the moved-back loop after the kills), `_h_plan_reopen` (every
loop). The op is recorded with `conflict` set and an empty inverse; the store is changed;
replay repeats the same partial mutation, so `replay == store` still holds and the test
suite cannot see it. `test_conflicts_are_recorded_and_state_untouched` only exercises
conflicts that are raised before any mutation.

```python
def test_conflicting_segment_delete_mutates_then_reports_nothing():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "abc", after="h1")
    before = copy.deepcopy(s.state_fingerprint())
    bad = apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                        {"segments": [[ins.id, 0, 3], ["ghost:1", 0, 1]]}))
    assert bad.conflict is not None and bad.inverse == []
    assert T(s, "p1") == "" and s.state_fingerprint() != before   # BUG: state changed, ledger says nothing did
    assert replay(s).state_fingerprint() == s.state_fingerprint() # I6 "holds" because replay repeats the lie
```

Fix: two-phase handlers. Resolve everything first into a worklist of `(Text, refs)` (every
`locate_char`, every `store.blocks[...]`, every `holds`), then mutate. For the loops above
that is one list comprehension before the loop. The general alternative, snapshot and
roll back the store on exception, costs O(document) per op and is not worth it.

### A5. `plan.accept` with a `subset` executes every `suggest_del`, including the rejected ones

`_h_plan_accept` computes `accepted`/`rejected` over `plan.ops` and kills the rejected
*inserts*, then runs *all* of `_suggest_marks(store, pid)` regardless. A rejected
`text.mark(suggest_del)` op is therefore executed, not removed. Also a rejected plan
`block.add` is left live and empty (reject's `emptied` rule is not applied here).

```python
def test_subset_accept_executes_unaccepted_suggest_del():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "keep this", after="h1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    sd = apply(s, ag.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                        {"kind": SUGGEST_DEL, "attrs": {"plan": "pl"}, "start": [ins.id, 5], "end": [ins.id, 8], "on": True}, plan="pl"))
    i1 = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 8], "text": "!"}, plan="pl"))
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {"subset": [i1.id]}))
    assert acc.payload["rejected"] == [sd.id]
    assert T(s, "p1") == "keep !"                # BUG: "this" struck although its strike was rejected
```

Fix: iterate the plan's `text.mark` ops (their canonical payload carries `bid`, `path`,
`added`), execute the ones in `accepted`, remove the ones in `rejected`. That also
deletes `_suggest_marks` (see C3). Apply the `emptied` rule to rejected adds.

### A6. The inverse of `plan.reopen` forgets the subset, so redo accepts everything

`_h_plan_reopen` returns `[{"kind": verdict, "payload": {}}]`. Inverse of inverse is not
the original for a subset accept.

```python
def test_reopen_inverse_drops_subset():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "base", after="h1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    i1 = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 3], "text": " A"}, plan="pl"))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [i1.id, 1], "text": " B"}, plan="pl"))
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {"subset": [i1.id]}))
    assert T(s, "p1") == "base A"
    (ro,) = invert(s, m, acc)
    redo = invert(s, m, ro)
    assert redo[0].conflict is None
    assert T(s, "p1") == "base A B"              # BUG: expected "base A"
```

Fix: put `subset` (the closure actually accepted) into the reopen payload and echo it in
reopen's inverse.

### A7. `block.move`'s inverse restores a raw rank that may now be taken; the next placement then conflicts

`rank.between(lo, hi)` is deterministic, so a block added between the same neighbours
after the move gets the moved block's old key. Undo writes the old key back:
two blocks share a rank, `_order` breaks the tie by id, and the next `_place` after
either of them calls `between(r, r)` and raises `RankError`, which surfaces as a conflict
on an innocent `block.add`.

```python
def test_move_undo_rank_collision_then_place_fails():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1")
    paragraph(m, s, "c", "c", after="a")
    paragraph(m, s, "b", "b", after="a")          # b between a and c
    rb = s.blocks["b"].rank
    mv = apply(s, m.op(K.BLOCK_MOVE, {"bid": "b"}, {"after": "c"}))
    paragraph(m, s, "y", "y", after="a")          # y gets b's old key (same neighbours, same midpoint)
    assert s.blocks["y"].rank == rb
    un = invert(s, m, mv)
    assert un[0].conflict is None
    assert s.blocks["b"].rank == s.blocks["y"].rank                       # BUG: duplicate rank
    nxt = apply(s, m.op(K.BLOCK_ADD, {"bid": "z"}, {"kind": PARAGRAPH, "after": "b"}))
    assert nxt.conflict is not None and "rank" in nxt.conflict          # BUG: a plain add conflicts
```

The same restore path is used by `plan.reject` (`moved_back`) and `plan.reopen`.

Fix: a rank is derived; the *position* is the intent. In the restore path, if
`(rank, other) in _order` for `other != bid`, re-derive with `between(prev, next)` around
the recorded rank. Cheaper still: make `Store.set_rank` refuse a collision and have the
restore path fall back to `between`. Either way `_order` must never hold two equal keys.

### A8. `block.set(kind)` round trip loses the heading level

`_h_block_set` records only `prev = b.kind`; heading → paragraph zeroes `level`, and the
inverse's heading ← paragraph sets it to 1.

```python
def test_set_kind_inverse_loses_level():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top", level=3)
    sk = apply(s, m.op(K.BLOCK_SET, {"bid": "h1", "path": "kind"}, {"value": PARAGRAPH}))
    invert(s, m, sk)
    assert s.blocks["h1"].kind == HEADING and s.blocks["h1"].level == 1   # BUG: was 3
```

Fix: canonical `{"value", "prev", "prev_level"}`; the inverse is `[set kind, set level]`
or `set kind` with a `level` field the handler honours.

### A9. `plan.reject` reverts moves only; a plan's `block.split` half stays as an empty ghost, a plan's `block.set` sticks, and a *later human move* is clobbered

`PLANNABLE` admits `block.split`, `block.set` and `block.move`, but reject undoes only
moves (and restores their pre-plan ranks unconditionally, over any move a person made
since).

```python
def test_reject_does_not_undo_split_or_set():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top", level=1)
    ins = paragraph(m, s, "p1", "one", after="h1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    apply(s, ag.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 2], "new": "p2"}, plan="pl"))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p2", "path": "text"}, {"after": [ins.id, 2], "text": "xx"}, plan="pl"))
    apply(s, ag.op(K.BLOCK_SET, {"bid": "h1", "path": "level"}, {"value": 3}, plan="pl"))
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert [b.id for b in s.ordered()] == ["h1", "p1", "p2"] and T(s, "p2") == ""   # BUG: empty ghost stays live
    assert s.blocks["h1"].level == 3                                                # BUG: plan's level change stuck

def test_reject_clobbers_later_human_move():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1"); paragraph(m, s, "b", "b", after="a"); paragraph(m, s, "c", "c", after="b")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="pl"))
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}))                 # human: a between b and c
    assert [x.id for x in s.ordered()] == ["h1", "b", "a", "c"]
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert [x.id for x in s.ordered()] == ["h1", "a", "b", "c"]                # BUG: the person's move is gone
```

Fix: define reject as "apply the recorded inverse of each plan op, in reverse", with
three refinements: inserts are killed by identity (A2), a block the plan created by
`add` *or* `split` that is now empty dies, and a `block.move` whose block was re-ranked
by a later non-plan op is skipped. The last needs `Store.rank_head[bid]` (pos of the last
rank change), a one-line addition to `set_rank`. Or shrink `PLANNABLE` to what reject can
honestly revert; `block.split` is needed by ingest step 2, so the split rule is required
either way.

### A10. `block.split` with a dead `new` id silently revives it (I4)

`revive = new in store.blocks` is meant for the merge inverse (`new_takes` set) but fires
for any dead id, overwriting its kind and level.

```python
def test_split_revives_unrelated_dead_block():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "abcd", after="h1")
    heading(m, s, "h9", "Gone", after="p1")
    apply(s, m.op(K.BLOCK_DELETE, {"bid": "h9"}, {}))
    sp = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 1], "new": "h9"}))
    assert sp.conflict is None and s.blocks["h9"].live and s.blocks["h9"].kind == PARAGRAPH   # BUG: duplicate_block
```

Fix: `if revive and not forced: raise Conflict("duplicate_block", new)`.

### A11. Undo of an older hand-off pops the newest hand-off id

`_h_feature_handoff` restore path does `w.handoffs.pop()` whatever hand-off is being
undone.

```python
def test_handoff_undo_pops_wrong_id():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    h1 = apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "one"}))
    h2 = apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "two"}))
    invert(s, m, h1)
    assert s.watermarks("h1").handoffs == [h1.id]                  # BUG: h2 was dropped
```

Fix: put the hand-off op id in the restore payload and remove that id; or drop the
`handoffs`/`realizations` lists entirely (they are not in the fingerprint and nothing reads
them; `directive(f)` can find hand-offs from the log by kind and fid).

### A12. Replay without the authority policy lands the forged write

Not a kernel bug but a trap the test helper sets: `replay()` builds `Store()` with no
policy, so a `conflict="authority"` op applies on rebuild and `replay == store` fails.
This is exactly the F1 scenario from the design review. Either `Store` carries the policy
and `replay` copies it, or (better) replay of an op that *already carries* `conflict`
still re-evaluates, which is what the spec wants, so the role table must be an input to
rebuild.

```python
def test_replay_without_policy_breaks_i6():
    def policy(op): return "suggest" if op.actor == "human:contrib" and op.plan is None else None
    m, s = Mint(), Store(policy)
    heading(m, s, "h1", "Top")
    c = Mint("c", "human:contrib")
    apply(s, c.op(K.TEXT_INSERT, {"bid": "h1", "path": "text"}, {"after": START, "text": "X"}))
    assert replay(s).state_fingerprint() != s.state_fingerprint()   # the forged write lands on rebuild
```

### A13. Smaller, confirmed

- **Text ops on a dead block are inconsistent.** `text.insert` refuses (`dead_block`);
  `text.delete` and `text.mark` proceed. Pick one. Refuse all three; `text.restore` and
  `block.restore` must still work so a reversed batch undo can bring a block and its text
  back.
- **`bind.attach` onto a dead heading succeeds.** `_feature` checks kind only. Spec §2: a
  dead heading is not a feature. Refuse attach; keep detach and refresh (the delete batch
  needs detach after the delete has run).
- **`text.delete` whose range spans a concurrent split conflicts** (`range_spans_blocks`).
  Spec §3 says a split under a concurrent text op costs nothing; the wire form is a range
  and the client cannot know about the split. See B8 for which side should move.
- **`Text.restore` after split + move + merge reorders the same op's characters** so the
  recorded `(op, k0, len)` walks a reversed range and raises. Exotic; the fix in A3
  (kill/revive by `s.op` over the whole sequence, no positional walk) removes the walk
  and the failure with it.
- **Bold across a tombstone gap stays two marks; a link over a same-href link fragments
  into three.** `_adjacent` is adjacency in the full sequence, so dead characters between
  two bolds keep them apart, and `EXCLUSIVE` displaces even a mark with identical attrs.
  Cosmetic if the projection merges; wrong if export renders marks as written. Treat
  "same kind and attrs" as mergeable for `link`/`cite` too, and define adjacency over
  live characters.
- **`_walk` has dead code**: the `if ei == si - 1: ei += 1` adjustment is immediately
  overwritten by `ei, eo = self.locate(end)`.
- **`text.mark(suggest_del)` does not pin `attrs.plan` to `op.plan`**; `_suggest_marks`
  keys on `attrs.plan`, so a plan op can strike text on another plan's behalf. Set it.
- **`block.set` on a reserved path** (`rank`, `id`, `dead`) writes into `content`.
  Refuse.
- **Conflict strings are not stable identifiers** for `KeyError`/`AnchorError`/`MarkError`
  (`"anchorerror: ('s:1', 9)"`). Map to `unknown_anchor`, `no_block`, `mark_partial`.
- **`apply` on an already-applied id returns the stored op but leaves the caller's
  object untouched**; a caller holding `op` sees `inverse == []`. Return the stored one
  (as now) *and* document it, or copy the recorded fields onto the argument.

---

## B. Spec deviations

Which side should change is stated per item.

- **B1. `block.merge` refuses only when the dying block is a heading** (`b.kind == HEADING`);
  the spec refuses if *either* is. Merging a paragraph into a heading makes body text
  title text and is never wanted. **Code changes**: `if a.kind == HEADING or b.kind == HEADING`.
  The random soup test currently relies on the permissive rule (it merges a paragraph
  into whatever precedes it); it will simply see a conflict.
- **B2. `block.delete` range.** Spec §3 says "computes its range the same way" as move
  (the dominated range) and two sentences later "does not touch descendant headings".
  The code implements the second (`body_range`: heading plus following non-heading
  blocks). The second is the one the design review argued for (A7/A8: promotion is
  optional cosmetics, so descendants survive). **Spec changes**: say "its own body: itself
  and the following non-heading blocks; a gesture deleting a subtree emits one
  `block.delete` per descendant heading".
- **B3. Three op kinds the spec does not have**: `text.restore`, `block.restore`,
  `plan.reopen`. They are the reified inverses, and §3.1 makes undo "the inverse as the
  person's own batch", so they must be ops. **Spec changes**: list them (seventeen
  primitives plus three inverse kinds), and state that `plan.reopen` is a verdict for
  authority purposes (`ops.VERDICTS` should include it; today it is defined in
  `apply.py` and special-cased past `K.KINDS`).
- **B4. `plan.accept` for `kind=delete` deletes inline** rather than "emits the delete
  batch". Inline is better (one op, one inverse), but the spec's companion rule, that a
  deleted heading's bindings are detached by ops in the same batch `caused_by` the
  delete, now has no emitter: nothing detaches. **Spec changes** to "accept performs the
  delete and records `deleted_blocks`; the accepting gesture emits the detaches
  `caused_by` the accept".
- **B5. `plan.reject` semantics.** Spec: kill live insertions, remove `suggest_del`, kill
  emptied added blocks. Code additionally reverts moves (good) but not `split` or `set`
  (A9). **Both change**: spec says "reject applies the inverse of every plan op in
  reverse, except that an insert is killed by identity and a later trunk re-rank of a
  moved block is not undone"; code does that.
- **B6. Inverses of the watermarks.** Spec table: `feature.handoff` inverse "previous
  frontier", `feature.realized` inverse "previous frontier". Code: hand-off restores
  (breaking I9 by design), realized has no inverse. I9 says frontiers only grow. **Spec
  changes**: `feature.realized` has no inverse (monotone); the undo of a hand-off is the
  one sanctioned regression and is applied only as the person's own undo of their own
  hand-off batch.
- **B7. `block.merge` records `at` as the survivor's last character live or dead**; spec
  says last *live* character. The code's choice is the correct one (splitting at the last
  live char would move trailing tombstones to the wrong half and change the sequence).
  **Spec changes**.
- **B8. Anchor resolution is by identity for `text.insert`/`delete`/`mark` but not for
  `block.split.at`**, which uses the client's `bid` and raises when `at` has moved. And a
  delete *range* whose ends are in two blocks after a split conflicts (A13). **Code
  changes** for split (resolve `at` through `locate_char`, it is two lines). **Spec
  changes** for the range: "a range whose ends resolve to different blocks is a recorded
  conflict; the client re-reads and resends per block". Honouring it in the applier would
  mean guessing which blocks lie between, which I5 forbids.
- **B9. `feature.realized` "emitted by the daemon, not the agent"** is unenforced in the
  kernel; the policy hook is the only place. Fine, but the default policy that ships with
  the daemon must deny `feature.realized` from any `agent:*` actor, and `ops.py`'s comment
  on `VERDICTS` should say so.
- **B10. `plan.open` has no inverse** (spec: "close"). A person's undo of a batch that
  opened a plan leaves an open empty plan. Either the spec drops the inverse (a plan with
  no ops is harmless and `plan.reject` closes it) or the code records `plan.close`.
  Recommend the spec drops it.
- **B11. NFC and `k`.** Only `text.insert` normalizes; `block.add.texts` (snapshot) and mark
  attrs do not. Acceptable, since those are already-normalized data. Worth one sentence in
  the spec: `k` counts scalars *of the NFC form*, so a client computing anchors into text
  it is about to send must normalize first or its `k` is off by the composition delta.
  Python's `len` on `str` is scalars, so the kernel side is right.
- **B12. `text.insert` accepts a `k0`** the spec does not have. One op mints one segment;
  there is no second field to offset into. Remove it (D).
- **B13. `Frontier` when `covers` is omitted** defaults to `store.frontier` (hand-off) or the
  hand-off frontier (realized). Sensible defaults the spec does not state; write them
  down, since the canonical payload freezes them.

---

## C. Complexity

Measured on a synthetic document (one heading per four blocks, 128-char paragraphs);
each op is O(N) in the paths named, and the numbers scale linearly (six times the
blocks, six times the time):

| op | 500 blocks | 3000 blocks |
|---|---|---|
| `text.insert` | 0.01 ms | 0.01 ms |
| `block.move` (one paragraph) | 0.10 ms | 0.56 ms |
| `Store.feature_of` | 0.08 ms | 0.49 ms |
| `plan.open` + one mark + `plan.accept` | 0.21 ms | 1.19 ms |

Not urgent at these sizes, but `feature_of` runs per projection row and `block.move` per
drag frame, and each fix is a few lines.

- **C1. `apply`'s idempotent path** `next(o for o in store.ops if o.id == op.id)` and
  `_plan_text_ops`'s `{o.id: o for o in store.ops}` (rebuilt on *every* accept/reject)
  are O(all ops). Fix: `Store.by_id: dict[OpId, Op]`, filled in `record`.
- **C2. `block.move`, `block.merge`, `block.delete`, `plan.accept(delete)`** all do
  `live = store.ordered()` (materializes N blocks) then `live.index(b)` and, in move, two
  comprehensions over `_order`. Fix: `bisect_left(_order, (b.rank, b.id))` gives the
  start; walk forward over `_order` skipping dead entries to compute `dominated`/
  `body_range` and the `hi` bound. O(range + interleaved dead).
- **C3. `_suggest_marks` scans every block and every mark** per verdict. Fix: the plan's
  own `text.mark` ops carry canonical `bid`/`path`/`added`; iterate `plan.ops` (also what
  A5 needs). Delete `_suggest_marks`.
- **C4. `Store.feature_of`** walks the whole live order from the start. Fix: bisect to the
  block's position and walk *backwards* to the nearest live heading, O(distance). Keep a
  headings-only sorted list (07 §D) only if `parent_of` is ever called in a loop; today it
  has no callers.
- **C5. `Text.set_mark` and `Text.split` are O(marks × segs)**: `_overlaps` costs four
  `locate` calls, `split` two `holds` per mark. Fix: locate each mark's endpoints once
  into a local dict, or in `split`, compare `locate(start)[0] < cut`. Paragraph-sized
  today; mark it `ponytail:` and move on.
- **C6. Rank growth at the tail.** `_place` and `_ranks_between` use `between(lo, "")`,
  which halves the remaining space; `rank.append_after` exists for exactly this case and
  is unused. Appending a 50-block feature at the end grows keys by roughly one character
  per five blocks. Fix: `hi == "" → append_after(cur)`.
- **C7. `Block` and `Seg` are `@dataclass` with value equality**; `live.index(b)` compares
  field by field (short-circuits on `id`, so cheap, but it is a trap: two blocks with equal
  content compare equal). `eq=False` on `Block` and `Seg`; keep it on `Mark` (`m not in
  self.marks` relies on it).
- **C8. `char_index` only grows** (split and merge add the new holder and never remove
  the old). Harmless with the `holds()` filter, but every stale entry is one more
  O(segs) `holds()` per lookup on that op. Remove the old `(bid, path)` when a split or
  merge moves *all* of an op's segments out.

---

## D. Cleanliness

- **Dead code**: `WHOLE` imported in `apply.py` and unused; `apply_all` defined and never
  called (the test imports it and never uses it); `Op.body()` unused while every handler
  hand-rolls the same three-key dict; `Store.order_index`, `Store.ops_since`,
  `doc.parent_of`, `ids.is_char_ref`, `ids.split_op_id`, `ids.new_batch_id`,
  `rank.append_after` have no callers. Delete or use (`append_after`: C6; `body()`: below).
- **`_ref`/`_json_ref` and `Mark.to_json`** are three spellings of one conversion
  (`list(m.start)` in `_h_text_mark`'s inverse bypasses `_json_ref`). One pair, in `ids.py`,
  used everywhere.
- **The inverse-body convention** is `list[dict]` of `{kind, target, payload}`. Fine as a
  wire shape, but it should be *the* shape: make `Op.body()` produce it and have `invert`
  and the handlers use it, or drop `body()`. A `TypedDict OpBody` costs nothing and lets
  the reader see that inverses carry no `plan`/`caused_by` (which is right: an undo is the
  person's own untagged op).
- **`_h_block_split`'s revive and fresh branches** each compute `rank` with the same
  expression and set `content[path]`; hoist `rank` above the branch, then the branch is
  four lines. The `new_first` / `new_takes` rule deserves the comment it half has: "both
  halves empty → original keeps the (empty) head".
- **`PLAN_REOPEN` lives in `apply.py`** and `apply` checks `op.kind not in K.KINDS and
  op.kind != PLAN_REOPEN`. Move it to `ops.py`, add it to `KINDS` and `VERDICTS`.
- **`_h_block_set` sets `store.structural_head = store.pos + 1`** by hand to match what
  `record` will compute, because `BLOCK_SET` is not in `STRUCTURAL`. Have handlers return
  a `structural: bool` (or `apply` derive it from `op.kind in STRUCTURAL or (BLOCK_SET and
  path in {"kind","level"})`).
- **`Store.marks` holds watermarks** while `Text.marks` holds marks. Rename to
  `Store.watermarks_by_fid` (the accessor is already `watermarks()`).
- **`Watermarks.handoffs`/`realizations`** are bookkeeping nobody reads and one path
  mis-pops (A11). Drop them; the log has the ops.
- **`Store.state_fingerprint`** is the right *shape* of equality: it includes dead blocks
  with full text (tombstones are truth), `Seg.at` (the spec wants snapshots
  self-describing), bindings, plans and both frontiers, and excludes the caches and every
  op's `conflict`, which is what §9 says "up to the conflict field" means. Two fixes: mark
  lists are order-sensitive, so `replay(A ++ B) == replay(B ++ A)` will fail on mark
  order alone; sort marks by `(locate(start), kind, attrs)` in the fingerprint. And
  `Plan.ops` is a list in apply order, which the same property will also disturb; compare
  it as a set.
- **`Mark.actor`**: present, good (the directive builder needs "who bolded this"). But
  `_h_text_mark` takes `actor` from the *wire payload* when present
  (`op.payload.get("actor") or op.actor`) so that the inverse can restore a displaced
  mark's original author. That also lets any client claim a person bolded a span. Restrict
  the override to ops whose `actor` is an owner, or carry the restored mark's actor in a
  field the policy hook can see and deny for `suggest`.
- **`k0` on `text.insert`** is dead flexibility (B12). Remove the parameter from
  `Text.insert` too.
- **`rank.py`'s module docstring** is v1 prose (Loop B's base check, `feature_writers`).
  Trim to what the calculus needs: the alphabet, the no-trailing-zero rule, growth.
- **`Conflict` messages**: `Conflict("bad_anchor", repr(v))` and friends put a repr in the
  exception text but only `.code` reaches the op. Good. Make the non-`Conflict` exceptions
  go through the same table (A13).

---

## E. Test gaps

Spec §9's pinned properties, against what exists:

| property | status |
|---|---|
| `apply ∘ inverse = identity` per op over random states | partial: the soup checks the *live view* (id, kind, level, text, order) only, never marks or ranks, over 7 of 20 kinds; `block.delete`, `block.set`, bindings, plans, hand-offs are not in the soup |
| `replay(log) == store` after every apply path incl. conflicts | partial: end of soup + three hand-picked conflicts; no conflict inside a loop (A4); no policy on replay (A12) |
| random sequences preserve I1, I4, I8, I9 | I1 yes (`test_text`); I4 one case; I8, I9 none |
| `ingest` then `text == new_text` | not implemented yet |
| `export → import → export` fixed point | not implemented yet |
| `replay(A ++ B) == replay(B ++ A)` for disjoint anchors | none |
| apply touches only the named blocks (counted) | none, though `touched` is already computed and is the assertion |
| Python/TypeScript parity | not yet applicable |
| `timeline` un-apply equals prefix replay | none, and the machinery exists |
| split/merge fuzzing with marks spanning the cut | one case in `test_text`; the soup splits and merges but never checks marks |

Three tests worth writing first. Each would have caught several §A items.

**T1. Un-apply equals prefix replay (pins inverse-of-inverse, marks, ranks, plans).**
Catches A6, A7, A8, and any handler whose inverse is not exact.

```python
def test_unapply_equals_prefix_replay():
    # build any log; the random soup's store works, so reuse it
    s = _soup(steps=200, seed=3)                       # extract the soup body into a helper returning the store
    log = [Op.from_json(copy.deepcopy(o.to_json())) for o in s.ops]
    for k in (len(log) // 4, len(log) // 2, len(log) - 3):
        probe = replay(s)                              # full state
        undo = Mint("u", "human:a")
        for o in reversed(log[k:]):
            for body in reversed(o.inverse):           # inverse bodies undo in reverse too
                un = apply(probe, undo.op(body["kind"], body["target"], body["payload"]))
                assert un.conflict is None, (o.kind, body, un.conflict)
        prefix = Store()
        for o in log[:k]:
            apply(prefix, Op.from_json(copy.deepcopy(o.to_json())))
        assert probe.state_fingerprint() == prefix.state_fingerprint(), k
```

(`state_fingerprint` must first become mark-order-insensitive, D; and `Plan.status` after
un-applying a verdict is `open`, which `reopen` does.)

**T2. A conflict never moves the state.** Catches A4 across every handler. Corrupt one
anchor in a well-formed op at random and assert nothing changed.

```python
def test_conflict_leaves_state_untouched_fuzz():
    rng = random.Random(5)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root"); p = paragraph(m, s, "p1", "seed text", after="h1")
    ag = Mint("a", "agent:x"); apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    for step in range(300):
        op = _random_op(rng, m, s, step)               # the soup's generator, extracted
        if rng.random() < 0.3:                         # poison one anchor or id
            key = rng.choice([k for k in ("after", "from", "to", "start", "end", "at") if k in op.payload] or ["bid"])
            if key == "bid": op.target["bid"] = "ghost"
            else: op.payload[key] = ["ghost:1", 0]
            if op.kind == K.TEXT_DELETE and rng.random() < 0.5:
                op.payload = {"segments": [[p.id, 0, 2], ["ghost:1", 0, 1]]}
        before = copy.deepcopy(s.state_fingerprint())
        done = apply(s, op)
        if done.conflict is not None:
            assert s.state_fingerprint() == before, (op.kind, done.conflict)
            assert done.inverse == []
```

**T3. Verdicts find their characters under concurrent structure.** Catches A1, A2, A5.

```python
def test_verdicts_resolve_by_identity_under_split_merge_move():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "alpha beta gamma", after="h1")
    paragraph(m, s, "p2", "delta", after="p1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    si = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 15], "text": " epsilon"}, plan="pl"))
    sd = apply(s, ag.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                        {"kind": SUGGEST_DEL, "attrs": {"plan": "pl"}, "start": [ins.id, 6], "end": [ins.id, 9], "on": True}, plan="pl"))
    # The person restructures around the suggestion.
    apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 4], "new": "p3"}))    # "alpha" | " beta gamma epsilon"
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "p3"}, {"after": "p2"}))
    apply(s, m.op(K.BLOCK_MERGE, {"into": "p2", "bid": "p3"}, {}))                                     # p2 = "delta beta gamma epsilon"
    snap = replay(s)
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert rj.conflict is None
    assert not [g for b in s.blocks.values() for g in b.text().segs if g.plan == "pl" and g.live]   # no plan ink survives anywhere
    assert not [mk for b in s.blocks.values() for mk in b.text().marks if mk.kind == SUGGEST_DEL]
    acc = apply(snap, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {}))
    assert acc.conflict is None
    assert snap.blocks["p2"].text().live_text() == "delta  gamma epsilon"                            # struck "beta" dead, insert kept
    invert(snap, m, acc)
    assert snap.blocks["p2"].text().live_text() == "delta beta gamma epsilon"
```

Also cheap and worth adding: an I8/I9 property in the soup (after every op, at most one
live owner per `(file, symbol)`, and every feature's `handoff`/`realized` frontier is
≥ the previous one unless the op was a hand-off undo); and "apply touches only the
named blocks" as `assert touched ⊆ {named} ∪ canonical range` with the fingerprint of
every *other* block unchanged.

---

## F. Ranked changes

1. **One holder per character identity** (A1). In `_h_block_merge`, after the merge:
   `b.content[path] = Text()`; in `Store.locate_char`, prefer a live holder when several
   hold the ref. Two lines plus a test from A1.
2. **Re-find characters by identity, everywhere** (A2, A3). `Text.delete(..., only=op)`
   filter; `_h_text_delete` segments path passes `only=s_op`; `_kill_plan_inserts`
   iterates `store.char_index[o.id]` and kills `s.op == o.id` in each holder;
   `_h_plan_reopen` restores through `store.locate_char`. Deletes `_suggest_marks` (C3)
   by iterating the plan's mark ops instead.
3. **Two-phase handlers** (A4). In every handler with a loop over anchors or block ids,
   build the worklist (`locate_char`/`blocks[...]`/`holds`) before the first mutation.
   Then T2.
4. **Subset governs marks; reopen carries it** (A5, A6). Execute only accepted
   `text.mark(suggest_del)` ops, remove the rejected ones, apply the `emptied` rule to
   rejected adds; put `subset` in the reopen payload and echo it in its inverse.
5. **Ranks are re-derived on restore** (A7). `Store.set_rank` refuses a collision; the
   restore paths in `block.move`, `plan.reject`, `plan.reopen` fall back to
   `between(prev, next)` around the recorded rank.
6. **Reject is "the inverse of each plan op, reversed"** (A9), with the three refinements
   (identity kill, emptied add-or-split blocks die, skip a move re-ranked later via a new
   `rank_head`). Also restores `block.set`. Spec §3 updated to match (B5).
7. **`block.set(kind)` records `prev_level`** (A8). Three lines.
8. **Tighten the refusals** (A10, A13, B1): merge refuses when either block is a heading;
   split refuses a dead `new` unless `new_takes`; `text.delete`/`text.mark` on a dead
   block refuse like insert; `bind.attach` refuses a dead heading; `suggest_del` pins
   `attrs.plan = op.plan`; `block.set` refuses reserved paths; non-`Conflict` exceptions
   map to stable codes.
9. **Spec edits** (B2, B3, B4, B6, B7, B8 range, B10, B11, B13): delete covers own body;
   list the three inverse kinds and make `plan.reopen` a verdict; accept performs the
   delete and the gesture detaches; `feature.realized` has no inverse; merge's `at` is the
   last character live or dead; a range across blocks conflicts; `plan.open` has no
   inverse; `k` counts NFC scalars; default frontiers stated.
10. **Resolve `block.split.at` by identity** (B8). Two lines using `locate_char`.
11. **Complexity** (C1, C2, C4, C6, C8): `Store.by_id`; bisect-and-walk for ranges in
    move/merge/delete/accept; backward walk in `feature_of`; `append_after` when
    `hi == ""`; prune `char_index` on split/merge.
12. **Fingerprint order-insensitivity** (D): sort marks and compare `Plan.ops` as a set,
    then T1.
13. **Cleanliness** (D): delete the unused helpers and imports; one CharRef JSON helper;
    `PLAN_REOPEN` into `ops.py` and `VERDICTS`; handlers return `structural`; rename
    `Store.marks`; drop `handoffs`/`realizations`; remove `k0`; hoist the split branches;
    trim `rank.py`'s docstring.
14. **`replay` carries the policy** (A12), and T3.
