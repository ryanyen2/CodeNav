"""U5 — edits.json / inbox.json read-modify-write are concurrency-safe.

The ``codoc serve`` hub is a second concurrent writer of these channels (the
daemon is the other), so the read-modify-write cycles must not lose entries —
the lost-update class the architecture review flagged. These tests hammer the
mutators from many threads; the shared, reentrant FileLock is what makes every
write land. (Without it the interleaved read→modify→write drops entries.)
"""
from __future__ import annotations

import concurrent.futures as cf

from codoc.loop import edits, inbox
from codoc.loop.edits import EditAnnotation, Steer


def _run(fns: list) -> None:
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for fut in [ex.submit(f) for f in fns]:
            fut.result()


def test_concurrent_annotation_appends_lose_nothing(tmp_path):
    cd = str(tmp_path)
    n = 40
    fns = [
        (lambda i=i: edits.append_annotation(
            cd, EditAnnotation(feature_id=f"f-{i:03d}", fields=["title"],
                               actor="human", mode="pen", suggestion_id="", ts=0)))
        for i in range(n)
    ]
    _run(fns)
    got = edits.read_annotations(cd)
    assert set(got) == {f"f-{i:03d}" for i in range(n)}


def test_concurrent_mixed_list_writes_keep_siblings(tmp_path):
    cd = str(tmp_path)
    n = 25
    fns: list = []
    for i in range(n):
        fns.append(lambda i=i: edits.append_steer(
            cd, Steer(feature_id=f"f-{i}", text=f"note {i}", comment_id=f"c-{i}", ts=0)))
        fns.append(lambda i=i: edits.append_cancellation(cd, f"g-{i}"))
    _run(fns)
    assert len(edits.read_steers(cd)) == n
    assert len(edits.read_cancellations(cd)) == n


def test_drain_is_atomic_against_sibling_appends(tmp_path):
    cd = str(tmp_path)
    for i in range(20):
        edits.append_steer(cd, Steer(feature_id=f"s-{i}", text="x", comment_id="", ts=0))
    fns: list = [lambda: edits.drain_steers(cd)]
    fns += [(lambda i=i: edits.append_cancellation(cd, f"c-{i}")) for i in range(20)]
    _run(fns)
    # every cancellation survived the concurrent steer-drain (no sibling loss)
    assert len(edits.read_cancellations(cd)) == 20


def test_concurrent_verdict_appends_lose_nothing(tmp_path):
    cd = str(tmp_path)
    n = 40
    fns = [(lambda i=i: inbox.append_verdict(cd, f"e-{i:03d}", accept=True)) for i in range(n)]
    _run(fns)
    got = inbox.read_verdicts(cd)
    assert len({v.event_id for v in got}) == n
