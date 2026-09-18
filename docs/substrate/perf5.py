"""Review-5 cost measurements (run from the worktree with PYTHONPATH=.)."""
import json
import shutil
import tempfile
import time
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import block_entry, projection
from codoc.calculus.ids import START
from codoc.calculus.window import history_window
from codoc.substrate.publish import Publisher
from codoc.substrate.workspace import Workspace

N = 1500
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
for r in range(4):
    for i in range(N):
        ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": f"h{i}"}, "payload": {"file": f"f{i}.py", "symbol": f"f{i}.py::sym", "fingerprint": f"r{r}"}}], actor="loop")
build = time.perf_counter() - t0
s = ws.store
print(f"store: {len(s.ops)} ops, {len(s.headings())} features; built through Workspace in {build:.2f}s")


def bench(name, fn, n=1):
    t = time.perf_counter()
    for _ in range(n):
        out = fn()
    dt = (time.perf_counter() - t) / n
    print(f"{name:52s} {dt*1000:9.1f} ms")
    return out


# 1. split/merge cost vs store size (prune_index scans char_index = every op)
_n = [0]
def split_merge(i):
    def go():
        _n[0] += 1
        t = s.blocks[f"p{i}"].text()
        ref = list(t.live_refs())[5]
        sp = ws.submit([{"kind": K.BLOCK_SPLIT, "target": {"bid": f"p{i}", "path": "text"}, "payload": {"new": f"x{i}-{_n[0]}", "at": list(ref)}}], actor="human:local")[0]
        assert sp.conflict is None, sp.conflict
        mg = ws.submit([{"kind": K.BLOCK_MERGE, "target": {"into": f"p{i}", "bid": f"x{i}-{_n[0]}", "path": "text"}, "payload": {}}], actor="human:local")[0]
        assert mg.conflict is None, mg.conflict
    return go


def insert(i):
    def go():
        t = s.blocks[f"p{i}"].text()
        ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": f"p{i}", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " x"}}], actor="human:local")
    return go


bench("text.insert (one op, one block)", insert(700), 20)
bench("block.split + block.merge (two ops, two blocks)", split_merge(700), 5)

# 2. publish warm vs cold; one-paragraph edit between publishes
pub = Publisher(tmp / ".codoc", str(tmp))
bench("Publisher.publish COLD", lambda: pub.publish(s, force=True))
insert(701)()
bench("Publisher.publish WARM after one insert", lambda: pub.publish(s, force=True))
bench("Publisher.publish WARM, nothing moved (force)", lambda: pub.publish(s, force=True), 3)
bench("history_window(200)", lambda: history_window(s, 200), 5)
bench("projection() bare (no memo)", lambda: projection(s))

# 3. block_entry under open plans: every block re-derives every open plan's mark ops
plans = []
for k in range(20):
    pid = ws.open_plan(actor="loop", kind="edit", target=f"p{k}")
    plans.append(pid)
    t = s.blocks[f"p{k}"].text()
    refs = list(t.live_refs())
    for j in range(0, 30, 3):
        ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": f"p{k}", "path": "text"},
                    "payload": {"kind": "bold", "attrs": {}, "start": list(refs[j]), "end": list(refs[j + 1]), "on": True}}], actor="loop", plan=pid)
print(f"opened {len(plans)} plans with {sum(len(s.plans[p].ops) for p in plans)} mark ops")
bench("block_entry(p1400) with 20 open plans", lambda: block_entry(s, s.blocks["p1400"]), 20)
cold = Publisher(tmp / ".codoc", str(tmp))
bench("Publisher.publish COLD with 20 open plans", lambda: cold.publish(s, force=True))
keys = cold._feature_keys(s, set())
bench("  of which phases() COLD", lambda: Publisher(tmp / ".codoc", str(tmp)).phases(s, set(), keys))
c2 = Publisher(tmp / ".codoc", str(tmp)); ph = c2.phases(s, set(), keys)
bench("  of which projection() COLD (entries+claims)", lambda: c2.projection(s, ph, set(), keys))
from codoc.calculus.derive import plan_claims
bench("  plan_claims(h1400) once", lambda: plan_claims(s, "h1400"), 20)
pub.publish(s, force=True)                      # warm the memo under the plans
insert(702)()
bench("Publisher.publish WARM (1 insert) with 20 open plans", lambda: pub.publish(s, force=True))
bench("Publisher.publish WARM nothing moved, 20 plans", lambda: pub.publish(s, force=True), 3)

# 4. rebuild from disk
ws.close()
bench("Workspace() rebuild from disk (readonly)", lambda: Workspace(tmp / ".codoc", readonly=True))
size = sum(p.stat().st_size for p in (tmp / ".codoc" / "log").glob("*.jsonl"))
print(f"log size on disk: {size/1e6:.1f} MB for {len(s.ops)} ops ({size/len(s.ops):.0f} B/op)")
shutil.rmtree(tmp, ignore_errors=True)
