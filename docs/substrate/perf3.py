"""Synthetic 1,500-feature / ~13,500-op store, as review-2 measured (one paragraph
and one binding per feature, four refresh rounds), written through a Workspace so
the rebuild-from-disk path is measured too."""
import shutil
import sys
import tempfile
import time
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import _drifted, ground, history, ink, phase, plan_claims, projection
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.loops.realize import queued, render_queue
from codoc.substrate.publish import phases_of, publish, write_projection
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
    print(f"{name:34s} {dt*1000:9.1f} ms")
    return out


bench("_drifted(f)", lambda: _drifted(s, "h700"), 20)
bench("history(f)", lambda: history(s, "h700"), 20)
bench("ground(f)", lambda: ground(s, "h700"), 20)
bench("phase(f)", lambda: phase(s, "h700"), 20)
bench("phase x 1500 (phases_of)", lambda: phases_of(s))
bench("projection()", lambda: projection(s))
bench("export_markdown()", lambda: export_markdown(s))
bench("queued()", lambda: queued(s), 5)
bench("publish()", lambda: publish(tmp / ".codoc", s, root_dir=str(tmp)), 3)
ws.close()
bench("Workspace() rebuild from disk", lambda: Workspace(tmp / ".codoc", readonly=True))
bench("Workspace.rebuild() in-memory-open", lambda: ws.rebuild())
shutil.rmtree(tmp, ignore_errors=True)
