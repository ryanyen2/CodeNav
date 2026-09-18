"""Review-7 cost measurements: perf6's rows re-run at 9012665, plus the
scaling rows (run from the worktree with PYTHONPATH=.).

    python perf7.py            # full: perf6 rows at N=1500 + scaling at N=1500/3000
"""
import shutil
import tempfile
import time
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import block_entry, ground, phase, plan_claims, projection
from codoc.calculus.ids import START
from codoc.calculus.window import history_window
from codoc.substrate.publish import Publisher
from codoc.substrate.snapshot import write_snapshot
from codoc.substrate.workspace import Workspace


def bench(name, fn, n=1):
    t = time.perf_counter()
    for _ in range(n):
        out = fn()
    dt = (time.perf_counter() - t) / n
    print(f"{name:60s} {dt*1000:9.1f} ms")
    return out


def build(N, plans=0, refreshes=4):
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
    pids = []
    for k in range(plans):
        pid = ws.open_plan(actor="loop", kind="edit", target=f"p{k}")
        pids.append(pid)
        rr = list(ws.store.blocks[f"p{k}"].text().live_refs())
        for j in range(0, 30, 3):
            ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": f"p{k}", "path": "text"},
                        "payload": {"kind": "bold", "attrs": {}, "start": list(rr[j]), "end": list(rr[j + 1]), "on": True}}], actor="loop", plan=pid)
    print(f"\n[N={N}] {len(ws.store.ops)} ops, {len(ws.store.headings())} features, {plans} open plans; built in {time.perf_counter()-t0:.1f}s")
    return tmp, ws, pids


# ── perf6 rows, re-run ─────────────────────────────────────────────────────────
N = 1500
tmp, ws, _ = build(N)
s = ws.store


def insert(i):
    def go():
        t = s.blocks[f"p{i}"].text()
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": f"p{i}", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " x"}}], actor="human:local")
    return go


_n = [0]
def split_merge(i):
    def go():
        _n[0] += 1
        t = s.blocks[f"p{i}"].text()
        ref = list(t.live_refs())[5]
        ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": f"p{i}", "path": "text"}, "payload": {"new": f"x{i}-{_n[0]}", "at": list(ref)}}], actor="human:local")
        ws.submit([{"kind": K.BLOCK_MERGE, "target": {"into": f"p{i}", "bid": f"x{i}-{_n[0]}", "path": "text"}, "payload": {}}], actor="human:local")
    return go


bench("text.insert (one op)", insert(700), 20)
bench("block.split + block.merge", split_merge(700), 5)
pub = Publisher(tmp / ".codoc", str(tmp))
bench("Publisher.publish COLD, no plans", lambda: pub.publish(s, force=True))
insert(701)()
bench("Publisher.publish WARM after one insert", lambda: pub.publish(s, force=True))
bench("history_window(200)", lambda: history_window(s, 200), 5)
bench("phase(h700)", lambda: phase(s, "h700"), 50)
bench("ground(h700)", lambda: ground(s, "h700"), 50)
keys = pub._feature_keys(s, set())
bench("Publisher._feature_keys (all headings)", lambda: pub._feature_keys(s, set()), 5)
bench("store.feature_of(p) for every paragraph", lambda: [s.feature_of(f"p{i}") for i in range(N)], 3)
bench("store.body_ids(h) for every heading", lambda: [s.body_ids(f"h{i}") for i in range(N)], 3)
bench("store.ops_on(body(h700))", lambda: s.ops_on({"h700", "p700"}), 50)
ws.close()
bench("Workspace() rebuild from disk, no snapshot", lambda: Workspace(tmp / ".codoc", readonly=True))
w = Workspace(tmp / ".codoc"); t0 = time.perf_counter(); write_snapshot(tmp / ".codoc", w.store, w.clock)
print(f"{'write_snapshot':60s} {(time.perf_counter()-t0)*1000:9.1f} ms"); w.close()
bench("Workspace() rebuild from the snapshot alone", lambda: Workspace(tmp / ".codoc", readonly=True))
size = sum(p.stat().st_size for p in (tmp / ".codoc" / "log").glob("*.jsonl"))
print(f"snapshot on disk: {size/1e6:.1f} MB")
shutil.rmtree(tmp, ignore_errors=True)

# ── open plans ────────────────────────────────────────────────────────────────
tmp, ws, pids = build(N, plans=20)
s = ws.store
open_ids = {p.id for p in s.plans.values() if p.status == "open"}
bench("block_entry(p1400), 20 open plans", lambda: block_entry(s, s.blocks["p1400"]), 50)
bench("plan_claims(h1400), 20 open plans", lambda: plan_claims(s, "h1400"), 50)
cold = Publisher(tmp / ".codoc", str(tmp))
bench("Publisher.publish COLD, 20 open plans x 10 marks", lambda: cold.publish(s, force=True))
c2 = Publisher(tmp / ".codoc", str(tmp)); ph = c2.phases(s, set(), c2._feature_keys(s, set()))
bench("  of which projection() COLD (entries+claims)", lambda: c2.projection(s, ph, set(), c2._feature_keys(s, set())))
bench("  of which phases() COLD", lambda: Publisher(tmp / ".codoc", str(tmp)).phases(s, set(), c2._feature_keys(s, set())))
bench("  of which export() COLD", lambda: Publisher(tmp / ".codoc", str(tmp)).export(s))
# 200 undecidable open plans (their target heading deleted): never offered, never closed, always walked
for k in range(200):
    pid = ws.open_plan(actor="loop", kind="edit", target=f"p{k+100}")
    rr = list(s.blocks[f"p{k+100}"].text().live_refs())
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": f"p{k+100}", "path": "text"},
                "payload": {"kind": "bold", "attrs": {}, "start": list(rr[0]), "end": list(rr[3]), "on": True}}], actor="loop", plan=pid)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": f"h{k+100}"}, "payload": {}}], actor="human:local")
open_ids = {p.id for p in s.plans.values() if p.status == "open"}
print(f"open plans now {len(open_ids)} (200 on dead headings, never offered)")
cold = Publisher(tmp / ".codoc", str(tmp))
bench("Publisher.publish COLD, +200 undecidable open plans", lambda: cold.publish(s, force=True))
for pid in pids[:10]:
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
bench("history_window(200) with 10 rejects inside", lambda: history_window(s, 200), 5)
ws.close()
shutil.rmtree(tmp, ignore_errors=True)

# ── scaling: N=3000 for the rows suspected of being O(n²) ────────────────────
tmp, ws, _ = build(3000, refreshes=1)
s = ws.store
pub = Publisher(tmp / ".codoc", str(tmp))
bench("[N=3000] Publisher._feature_keys (all headings)", lambda: pub._feature_keys(s, set()), 3)
bench("[N=3000] store.feature_of(p) for every paragraph", lambda: [s.feature_of(f"p{i}") for i in range(3000)], 2)
bench("[N=3000] store.body_ids(h) for every heading", lambda: [s.body_ids(f"h{i}") for i in range(3000)], 2)
bench("[N=3000] Publisher.publish COLD, no plans", lambda: pub.publish(s, force=True))
ws.close()
shutil.rmtree(tmp, ignore_errors=True)
