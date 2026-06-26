"""U11 — the Notion bridge is a second writer to edits.json/inbox.json; the shared
filelock must prevent lost updates when it races the daemon's drain.
"""
from __future__ import annotations

import threading

from codoc.loop import edits as edits_channel
from codoc.loop import inbox
from codoc.model.event import NodeOp, NodeOpKind


def test_concurrent_node_ops_appends_no_lost_updates(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    per_thread = 25
    barrier = threading.Barrier(2)

    def writer(tag):
        barrier.wait()  # maximize contention
        for i in range(per_thread):
            edits_channel.append_node_ops(
                cd, [NodeOp(kind=NodeOpKind.AMEND, feature_id=f"{tag}-{i}", description="x")])

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    ops = edits_channel.read_node_ops(cd)
    fids = {op.feature_id for op in ops}
    # every appended op survived (no read-modify-write clobbered the other writer)
    assert len(ops) == 2 * per_thread
    assert len(fids) == 2 * per_thread


def test_concurrent_node_ops_and_steers_coexist(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    n = 20
    barrier = threading.Barrier(2)

    def write_ops():
        barrier.wait()
        for i in range(n):
            edits_channel.append_node_ops(
                cd, [NodeOp(kind=NodeOpKind.AMEND, feature_id=f"op-{i}", description="x")])

    def write_steers():
        barrier.wait()
        for i in range(n):
            edits_channel.append_steer(cd, edits_channel.Steer(feature_id=f"s-{i}", text="note"))

    t1 = threading.Thread(target=write_ops)
    t2 = threading.Thread(target=write_steers)
    t1.start(); t2.start(); t1.join(); t2.join()

    # the two lists in the same file did not clobber each other
    assert len(edits_channel.read_node_ops(cd)) == n
    assert len(edits_channel.read_steers(cd)) == n


def test_concurrent_verdicts_no_lost_updates(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    per_thread = 25
    barrier = threading.Barrier(2)

    def writer(tag):
        barrier.wait()
        for i in range(per_thread):
            inbox.append_verdict(cd, f"e-{tag}-{i}", accept=True)

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    verdicts = inbox.read_verdicts(cd)
    assert len({v.event_id for v in verdicts}) == 2 * per_thread
