"""Review-8 cost measurements at 18855c0: perf7's rows re-run, plus the rows
this round suspects (run from the worktree with PYTHONPATH=.).

    PYTHONPATH=. ../CodeNav/.venv/bin/python <this file>

Every number is one run on a noisy machine: read the RATIOS within one run.
"""
import shutil
import tempfile
import time
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import ground, open_plans_on, phase
from codoc.calculus.ids import START
from codoc.calculus.window import history_window
from codoc.substrate.publish import Publisher
from codoc.substrate.snapshot import settled_prefix, state_at, write_snapshot
from codoc.substrate.workspace import Workspace


def bench(name, fn, n=1):
    t = time.perf_counter()
    for _ in range(n):
        out = fn()
    dt = (time.perf_counter() - t) / n
    print(f"{name:66s} {dt*1000:9.1f} ms")
    return out


def build(N, plans=0, refreshes=4, decided=0):
    tmp = Path(tempfile.mkdtemp())
    ws = Workspace(tmp / ".codoc")
    t0 = time.perf_counter()
    last = START
    for i in range(N):
        h, p = f"h{i}", f"p{i}"
        ws.submit([
            {"kind": K.BLOCK_ADD, "target": {"bid": h}, "payload": {"kind": "heading", "level": 1, "after": last}},
            {"kind": K.TEXT_INSERT, "target": {"bid": h, "path": "text"}, "payload": {"after": START, "text": f"Feature {i}"}},
            {"kind": K.BLOCK_ADD, "target": {"bid": p}, "payload": {"kind": "paragraph", "after": h}},
            {"kind": K.TEXT_INSERT, "target": {"bid": p, "path": "text"}, "payload": {"after": START, "text": f"Paragraph {i} describes something useful."}},
            {"kind": K.BIND_ATTACH, "target": {"fid": h}, "payload": {"file": f"f{i}.py", "symbol": f"f{i}.py::sym", "fingerprint": "a"}},
        ], actor="loop")
        last = p
    for r in range(refreshes):
        for i in range(N):
            ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": f"h{i}"}, "payload": {"file": f"f{i}.py", "symbol": f"f{i}.py::sym", "fingerprint": f"r{r}"}}], actor="loop")
    for k in range(decided):
        # a year of answered proposals: one per feature, decided
        pid = ws.open_plan(actor="loop", kind="edit", target=f"p{k % N}")
        rr = list(ws.store.blocks[f"p{k % N}"].text().live_refs())
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": f"p{k % N}", "path": "text"}, "payload": {"after": list(rr[-1]), "text": " x"}}], actor="loop", plan=pid)
        ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    pids = []
    for k in range(plans):
        pid = ws.open_plan(actor="loop", kind="edit", target=f"p{k}")
        pids.append(pid)
        rr = list(ws.store.blocks[f"p{k}"].text().live_refs())
        for j in range(0, 30, 3):
            ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": f"p{k}", "path": "text"},
                        "payload": {"kind": "bold", "attrs": {}, "start": list(rr[j]), "end": list(rr[j + 1]), "on": True}}], actor="loop", plan=pid)
    print(f"\n[N={N} refreshes={refreshes} decided={decided} plans={plans}] {len(ws.store.ops)} ops, "
          f"{len(ws.store.headings())} features; built in {time.perf_counter()-t0:.1f}s")
    return tmp, ws, pids


# ── 1. perf7 rows, re-run (drifted tree: 4 refreshes) ─────────────────────────
N = 1500
tmp, ws, _ = build(N)
s = ws.store


def insert(i):
    def go():
        t = s.blocks[f"p{i}"].text()
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": f"p{i}", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " x"}}], actor="human:local")
    return go


bench("text.insert (one op)", insert(700), 20)
pub = Publisher(tmp / ".codoc", str(tmp))
bench("Publisher.publish COLD, no plans (drifted tree)", lambda: pub.publish(s, force=True))
insert(701)()
bench("Publisher.publish WARM after one insert", lambda: pub.publish(s, force=True))
bench("history_window(200)", lambda: history_window(s, 200), 5)
bench("phase(h700)", lambda: phase(s, "h700"), 50)
bench("ground(h700)", lambda: ground(s, "h700"), 50)
bench("open_plans_on(h700), 0 plans", lambda: open_plans_on(s, "h700"), 50)
bench("settled_prefix (every feature drifted)", lambda: settled_prefix(s), 3)
print(f"   -> cut = {settled_prefix(s)} of {len(s.ops)} ops ({100*settled_prefix(s)/len(s.ops):.1f}% foldable)")
ws.close()
w = Workspace(tmp / ".codoc"); t0 = time.perf_counter(); out = write_snapshot(tmp / ".codoc", w.store, w.clock)
print(f"{'write_snapshot (drifted tree)':66s} {(time.perf_counter()-t0)*1000:9.1f} ms  -> {out.name if out else None}"); w.close()
shutil.rmtree(tmp, ignore_errors=True)

# ── 2. the fold on a SETTLED tree (no refresh: every feature synced) ─────────
tmp, ws, _ = build(N, refreshes=0)
s = ws.store
bench("settled_prefix (every feature synced)", lambda: settled_prefix(s), 3)
print(f"   -> cut = {settled_prefix(s)} of {len(s.ops)} ops")
size_log = sum(p.stat().st_size for p in (tmp / ".codoc" / "log").glob("*.jsonl"))
ws.close()
bench("Workspace() rebuild from disk, no snapshot", lambda: Workspace(tmp / ".codoc", readonly=True))
w = Workspace(tmp / ".codoc"); t0 = time.perf_counter(); out = write_snapshot(tmp / ".codoc", w.store, w.clock)
print(f"{'write_snapshot (settled tree, folds whole)':66s} {(time.perf_counter()-t0)*1000:9.1f} ms  -> {out.name if out else None}"); w.close()
bench("Workspace() rebuild from the snapshot alone", lambda: Workspace(tmp / ".codoc", readonly=True))
size_snap = sum(p.stat().st_size for p in (tmp / ".codoc" / "log").glob("*.jsonl"))
print(f"   log {size_log/1e6:.1f} MB -> snapshot {size_snap/1e6:.1f} MB")
shutil.rmtree(tmp, ignore_errors=True)

# ── 3. a PARTIAL fold: one drifted feature in the middle → state_at replays ──
tmp, ws, _ = build(N, refreshes=0)
s = ws.store
ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": f"h{N//2}"}, "payload": {"file": f"f{N//2}.py", "symbol": f"f{N//2}.py::sym", "fingerprint": "z"}}], actor="loop")
cut = settled_prefix(s)
print(f"   one drifted feature at index {N//2}: cut = {cut} of {len(s.ops)} ops ({100*cut/len(s.ops):.1f}% foldable)")
bench("state_at(store, cut) — the replay a partial fold pays", lambda: state_at(s, cut))
ws.close()
w = Workspace(tmp / ".codoc"); t0 = time.perf_counter(); out = write_snapshot(tmp / ".codoc", w.store, w.clock)
print(f"{'write_snapshot (half the tree folds)':66s} {(time.perf_counter()-t0)*1000:9.1f} ms  -> {out.name if out else None}"); w.close()
shutil.rmtree(tmp, ignore_errors=True)

# ── 4. decided plans: open_plans_on walks EVERY plan per feature per publish ──
for decided in (0, 1500, 3000):
    tmp, ws, _ = build(N, refreshes=0, decided=decided)
    s = ws.store
    bench(f"open_plans_on(h700), {decided} decided plans", lambda: open_plans_on(s, "h700"), 50)
    bench(f"phase(h700), {decided} decided plans", lambda: phase(s, "h700"), 50)
    pub = Publisher(tmp / ".codoc", str(tmp))
    bench(f"Publisher.phases() COLD, {decided} decided plans", lambda: Publisher(tmp / ".codoc", str(tmp)).phases(s, set()), 1)
    bench(f"Publisher.publish COLD, {decided} decided plans", lambda: pub.publish(s, force=True))
    ws.close()
    shutil.rmtree(tmp, ignore_errors=True)

# ── 5. a verdict on a big tree: _proposed_marks scans every block ────────────
for n in (1500, 3000):
    tmp, ws, pids = build(n, refreshes=0, plans=20)
    s = ws.store
    t = time.perf_counter()
    for pid in pids[:10]:
        ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    print(f"{f'[N={n}] plan.accept (10 marks each), per verdict':66s} {(time.perf_counter()-t)*1000/10:9.1f} ms")
    t = time.perf_counter()
    for pid in pids[10:20]:
        ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    print(f"{f'[N={n}] plan.reject (10 marks each), per verdict':66s} {(time.perf_counter()-t)*1000/10:9.1f} ms")
    ws.close()
    shutil.rmtree(tmp, ignore_errors=True)

# ── 6. scaling of the publish itself ─────────────────────────────────────────
for n in (1500, 3000):
    tmp, ws, _ = build(n, refreshes=1)
    s = ws.store
    pub = Publisher(tmp / ".codoc", str(tmp))
    bench(f"[N={n}] Publisher.publish COLD, no plans", lambda: pub.publish(s, force=True))
    bench(f"[N={n}] Publisher._feature_keys", lambda: pub._feature_keys(s, set()), 3)
    bench(f"[N={n}] settled_prefix", lambda: settled_prefix(s), 2)
    ws.close()
    shutil.rmtree(tmp, ignore_errors=True)
