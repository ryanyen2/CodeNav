#!/usr/bin/env python3
"""The codoc arm's description edits, taken out of codoc's own change ledger.

    python3 ledger-actions.py <collected-folder>

Why this exists, and why it is not optional.

The interaction log records an edit to the description by watching a TEXT
DOCUMENT change. That works in the baseline, where the description is
`CLAUDE.md` and the participant types into an ordinary editor. It does not work
in the codoc arm at all: the tree is edited in a custom editor, which is a
webview, so no text document ever changes and no edit event is ever written.

Measured on the first pilot, from the merged stream:

    baseline   description edits   human 3 / agent 3
    codoc      description edits   human 0 / agent 0     <-- not a finding

The codoc workspace's ledger had a human amend in it the whole time. So the
figure the thesis rests on — who writes to the description — was counting one
arm and not the other, in the direction that makes codoc look like a document
nobody writes in. Anything computed from that comparison was wrong, and wrong
against the tool.

This reads the store and emits `{"ev": "codoc", ...}` lines in the logger's own
schema, which `actions-vocab.js` already knows how to map (`case 'codoc'`):
an amend/add/move/retire by a human becomes EDIT_DOC, by the loop AGENT_DOC, and
a verdict becomes ACCEPT or REJECT.

SEEDING IS EXCLUDED. A seeded workspace ships with its whole history already in
the ledger — on the pilot, 57 of 68 events were `bootstrap` and `translate`,
written days before the participant sat down. Counting those would swamp the
handful of events the session actually produced. Anything sourced from bootstrap
or translate is dropped, and so is anything stamped before the session began.

THE RECORDED SESSION IS EXCLUDED THE SAME WAY. Since the task redesign the
workspace also ships the ledger of the agent session the participant reviews,
stamped when the recording ran. `replay/play.py` writes `.codoc/replay.stamp` at
the moment it hands the workspace over, and everything older than the handover is
dropped, so what is left is what the participant did.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Written before the participant ever saw the workspace.
SEEDING_SOURCES = {"bootstrap", "translate"}

# The recorded session leaves its own ledger events in the shipped store, stamped
# when the recording ran. `play.py` writes `.codoc/replay.stamp` when it hands the
# workspace over, so anything older than the handover is either seeding or the
# recording, and only what comes after it is the participant's.


def handover_ms(codoc_dir: Path) -> int:
    stamp = codoc_dir / "replay.stamp"
    if not stamp.exists():
        return 0
    try:
        return int(json.loads(stamp.read_text())["handover_ms"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0

# The op kinds that are a change to what the description SAYS. attach/detach/
# refresh are bookkeeping — the vocabulary drops them too, and for the same
# reason: nobody decided anything by re-pointing a binding.
PROSE_KINDS = {"amend", "add_node", "move_node", "retire_node"}


def _ms(hlc) -> int:
    """Wall-clock milliseconds from an HLC, so it merges with the logger's `t`."""
    try:
        text = hlc.to_str() if hasattr(hlc, "to_str") else str(hlc)
        # HLC serializes as "<iso>|<counter>" or similar; take the leading stamp.
        head = text.split("|")[0].split("#")[0]
        dt = datetime.fromisoformat(head.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def events_for_workspace(codoc_dir: Path, ws: str, participant: str,
                         since_ms: int = 0) -> list[dict]:
    """Ledger events for one codoc workspace, as raw logger lines."""
    from codoc.store.db import open_store

    out: list[dict] = []
    since_ms = since_ms or handover_ms(codoc_dir)
    unstamped = 0
    store = open_store(str(codoc_dir))
    try:
        for e in store.recent_events(5000):
            if e.source in SEEDING_SOURCES:
                continue
            kind = e.op.kind.value
            t = _ms(e.at) if hasattr(e, "at") else 0
            if since_ms and not t:
                unstamped += 1
            if since_ms and t and t < since_ms:
                continue
            actor = (getattr(e, "actor", "") or "").lower()
            # `human` is the participant; everything else is the loop or an agent.
            who = "human" if actor == "human" else "loop"
            if kind in PROSE_KINDS:
                out.append({
                    "t": t, "p": participant, "ws": ws, "ev": "codoc",
                    "kind": kind, "actor": who, "feature": e.op.feature_id or "",
                    "source": e.source,
                })
        # Verdicts: an accept leaves an applied event citing the proposal it came
        # from, which is how `codoc_await_verdicts` recovers them too.
        for e in store.recent_events(5000):
            if e.source in SEEDING_SOURCES:
                continue
            caused = getattr(e, "caused_by", "") or ""
            if not caused.startswith("e-"):
                continue
            t = _ms(e.at) if hasattr(e, "at") else 0
            if since_ms and t and t < since_ms:
                continue
            out.append({
                "t": t, "p": participant, "ws": ws, "ev": "codoc",
                "kind": "verdict", "accept": True, "eventId": caused,
            })
    finally:
        store.close()
    if unstamped:
        print(f"  warning: {unstamped} event(s) in {ws} carry no readable timestamp, "
              "so the handover watermark could not be applied to them", file=sys.stderr)
    out.sort(key=lambda r: r["t"])
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).expanduser()
    meta = root / "collection.meta"
    participant = ""
    if meta.exists():
        for line in meta.read_text().splitlines():
            if line.startswith("participant:"):
                participant = line.split(":", 1)[1].strip()

    logs = root / "session-logs"
    logs.mkdir(exist_ok=True)
    found = 0
    for ws_dir in sorted(p for p in root.iterdir() if (p / ".codoc").is_dir()):
        ws = ws_dir.name
        rows = events_for_workspace(ws_dir / ".codoc", ws, participant)
        if not rows:
            print(f"{ws}: no session-time ledger events (seeding excluded)")
            continue
        found += 1
        dest = logs / f"ledger-{ws}.jsonl"
        dest.write_text("".join(json.dumps(r) + "\n" for r in rows))
        human = sum(1 for r in rows if r.get("actor") == "human")
        loop = sum(1 for r in rows if r.get("actor") == "loop")
        verdicts = sum(1 for r in rows if r.get("kind") == "verdict")
        print(f"{ws}: {len(rows)} ledger events "
              f"({human} by the person, {loop} by the loop, {verdicts} verdict(s))")
        print(f"  {dest}")
    if not found:
        print("no codoc workspace in this folder (a baseline-only collection is fine)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
