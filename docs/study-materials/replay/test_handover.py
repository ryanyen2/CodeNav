#!/usr/bin/env python3
"""What happens after the replay hands the workspace over.

    python3 test_handover.py ~/codoc-recording/scribe-codoc

The replay ends and the participant takes over. From that point the study is
measuring what they can do about the change: accept part of it, reject part of
it, correct the description, or ask for the code to follow. Each of those has to
set off the right next thing, or the participant does the work and nothing
happens, which looks identical to a participant who did nothing.

This drives the real store through each of them, using `codoc sync` rather than
the daemon so that nothing depends on timing. It is destructive, so it runs on a
copy of a workspace and never on one a participant is using.

What it checks, in order:

    accepting a proposal      applies it, and the proposal stops being pending
    rejecting a proposal      drops it, and the feature keeps the words it had
    editing a description     lands in the store and is what the tree exports
    commenting on a feature   becomes a directive an agent can act on

It exits non-zero on the first failure and says which of the four broke.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PASS, FAIL = "\033[32mok\033[0m", "\033[31mfail\033[0m"


class Broken(Exception):
    pass


def ok(message: str) -> None:
    print(f"  {PASS}    {message}")


def sync(workspace: Path) -> None:
    done = subprocess.run(["codoc", "sync"], cwd=workspace, capture_output=True,
                          text=True, timeout=600)
    if done.returncode != 0:
        raise Broken(f"codoc sync failed: {done.stderr.strip()[:300]}")


def sidecar(workspace: Path) -> dict:
    path = workspace / ".codoc" / "tree.index.json"
    return json.loads(path.read_text()) if path.exists() else {}


def pending(workspace: Path) -> dict:
    props = (sidecar(workspace).get("proposals") or {})
    out = dict(props.get("by_feature") or {})
    for add in props.get("adds") or []:
        out[f"add:{add.get('event_id')}"] = add
    return out


def store(workspace: Path):
    from codoc.store.db import open_store
    return open_store(str(workspace / ".codoc"))


def description_of(workspace: Path, fid: str) -> str:
    s = store(workspace)
    try:
        feature = s.get_feature(fid)
        return (feature.description or "") if feature else ""
    finally:
        s.close()


def any_feature(workspace: Path) -> str:
    s = store(workspace)
    try:
        features = s.list_features()
        if not features:
            raise Broken("the store holds no live feature")
        # The deepest one, because a leaf owns code and a theme near the root may
        # own none, and an edit to a feature with no bindings cannot ask for code
        # to follow.
        return sorted(features, key=lambda f: len(getattr(f, "path", "") or ""))[-1].id
    finally:
        s.close()


def append_command(workspace: Path, command: dict) -> None:
    from codoc.loop.edits import append_host_op
    append_host_op(str(workspace / ".codoc"), "appendCommand", command)


def check_accept(workspace: Path) -> None:
    from codoc.loop.inbox import host_verdicts_path
    before = pending(workspace)
    target = next((k for k in before if not k.startswith("add:")), None)
    if target is None:
        print("  (no proposal to accept in this recording, so the accept path is untested)")
        return
    event_id = before[target].get("event_id")
    path = Path(host_verdicts_path(str(workspace / ".codoc")))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event_id": event_id, "accept": True}) + "\n")
    sync(workspace)
    after = pending(workspace)
    if target in after and after[target].get("event_id") == event_id:
        raise Broken(f"accepting {event_id} left it pending, so the click did nothing")
    ok(f"accepting a proposal applies it and clears it ({event_id})")


def check_reject(workspace: Path) -> None:
    from codoc.loop.inbox import host_verdicts_path
    before = pending(workspace)
    target = next((k for k in before if not k.startswith("add:")), None)
    if target is None:
        print("  (no second proposal, so the reject path is untested)")
        return
    event_id = before[target].get("event_id")
    words = description_of(workspace, target)
    path = Path(host_verdicts_path(str(workspace / ".codoc")))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event_id": event_id, "accept": False}) + "\n")
    sync(workspace)
    if target in pending(workspace):
        raise Broken(f"rejecting {event_id} left it pending")
    if description_of(workspace, target) != words:
        raise Broken(f"rejecting {event_id} changed the feature anyway")
    ok(f"rejecting a proposal drops it and leaves the words alone ({event_id})")


def check_edit(workspace: Path) -> None:
    fid = any_feature(workspace)
    was = description_of(workspace, fid)
    now = (was + "\n\nThe reviewer added this sentence.").strip()
    append_command(workspace, {
        "id": "handover-edit-1", "kind": "set_description", "fid": fid,
        "baseRev": "", "base_text": was, "payload": {"description": now},
    })
    sync(workspace)
    stored = description_of(workspace, fid)
    if "The reviewer added this sentence." not in stored:
        raise Broken(f"the edit to {fid} never reached the store")
    exported = (workspace / ".codoc" / "tree.codoc").read_text()
    if "The reviewer added this sentence." not in exported:
        raise Broken("the edit reached the store but not the tree the reader sees")
    ok(f"an edit reaches the store and the exported tree ({fid})")

    # Replaying the same command must not apply twice. The daemon can re-drain a
    # command after a crash, and a doubled description is the shape that would
    # show up in a participant's tree with nobody having typed it.
    append_command(workspace, {
        "id": "handover-edit-1", "kind": "set_description", "fid": fid,
        "baseRev": "", "base_text": was, "payload": {"description": now + " Again."},
    })
    sync(workspace)
    if "Again." in description_of(workspace, fid):
        raise Broken("a replayed command applied a second time")
    ok("and replaying the same command changes nothing")


def check_comment(workspace: Path) -> None:
    from codoc.loop.edits import append_host_op
    fid = any_feature(workspace)
    append_host_op(str(workspace / ".codoc"), "appendSteer", {
        "feature_id": fid, "comment_id": "handover-comment-1",
        "text": "This is not what the description promises. Make the code match.",
        "anchor_text": "", "scope": "code",
    })
    sync(workspace)
    realize = workspace / ".codoc" / "realize.md"
    text = realize.read_text() if realize.exists() else ""
    if "Make the code match" not in text:
        raise Broken("a comment produced no directive an agent could act on")
    ok("a comment becomes a directive in the realize queue")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    source = Path(argv[0]).expanduser().resolve()
    if not (source / ".codoc").is_dir():
        print(f"{source} is not a codoc workspace", file=sys.stderr)
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="handover-"))
    workspace = scratch / source.name
    shutil.copytree(source, workspace, symlinks=True)
    print(f"driving a copy of {source.name}")
    try:
        for check in (check_accept, check_reject, check_edit, check_comment):
            check(workspace)
    except Broken as error:
        print(f"  {FAIL}  {error}")
        print(f"\nthe copy is left at {workspace} so the state can be read")
        return 1
    shutil.rmtree(scratch, ignore_errors=True)
    print("every action after the handover set off the next one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
