#!/usr/bin/env python3
"""Check that a finished session can actually answer the research questions.

    python3 check-session-complete.py ~/Downloads/codoc-study-p04

Point it at an unpacked session folder from `collect.sh`. It walks the measures
in `analysis-plan.md`, says for each one whether the data to compute it arrived,
and computes the ones that are computable so you can see they are not empty.

Run it while the participant is still on the call. A missing log is recoverable
in the next thirty seconds and unrecoverable the day after.

What it cannot see: your notes, the sign-off, the answers to the questions, and
the questionnaires. Those are on paper or in a form. It says so rather than
pretending the session is complete without them.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

DOC_FILES = {"tree.codoc", "CLAUDE.md"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.missing = 0

    def ok(self, measure: str, detail: str = "") -> None:
        self.rows.append(("have", measure, detail))

    def gap(self, measure: str, detail: str = "") -> None:
        self.rows.append(("MISSING", measure, detail))
        self.missing += 1

    def manual(self, measure: str, detail: str = "") -> None:
        self.rows.append(("by hand", measure, detail))

    def show(self, title: str) -> None:
        print(f"\n{title}")
        for state, measure, detail in self.rows:
            print(f"  {state:>8}  {measure}")
            if detail:
                print(f"            {detail}")


def load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def find(root: Path, *names: str) -> list[Path]:
    hits: list[Path] = []
    for name in names:
        hits.extend(p for p in root.rglob(name) if p.is_file())
    return hits


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).expanduser().resolve()
    if not root.is_dir():
        print(f"not a folder: {root}")
        return 2

    print(f"session: {root}")
    rep = Report()

    # ── the four machine-written sources ─────────────────────────────────────
    inter = find(root, "interaction*.jsonl")
    events = [e for p in inter for e in load_jsonl(p)]
    transcripts = find(root, "*.jsonl")
    transcripts = [p for p in transcripts if "interaction" not in p.name]
    projects = [p.parent for p in find(root, "pyproject.toml")]
    logdirs = [p for p in root.rglob("session.meta") if p.is_file()]

    print(f"\ninteraction log:  {len(events)} events from {len(inter)} file(s)")
    print(f"transcripts:      {len(transcripts)} file(s)")
    print(f"projects:         {len(projects)}  ({', '.join(p.name for p in projects) or 'none'})")
    print(f"session recorders:{len(logdirs)}")

    by_ev = Counter(e.get("ev") for e in events)
    if events:
        print(f"event kinds:      {dict(by_ev)}")

    # ── question 1: who writes what ──────────────────────────────────────────
    rep.rows.clear()
    edits = [e for e in events if e.get("ev") == "edit"]
    human_edits = [e for e in edits if e.get("active") and e.get("focused")]
    doc_edits = [e for e in edits if e.get("surface") == "document"]
    if edits:
        rep.ok("where each change originated",
               f"{len(edits)} edits, {len(human_edits)} of them typed into the active editor")
    else:
        rep.gap("where each change originated",
                "no edit events. The logger extension was not installed or not running.")

    # Snapshots of the description over time, wherever the recorder put them.
    doc_snaps = [p for p in root.rglob("*")
                 if p.name in DOC_FILES and "session-logs" in p.parts]
    if doc_edits or doc_snaps:
        rep.ok("what kind of edits people make to the description",
               f"{len(doc_edits)} edits to the description, {len(doc_snaps)} snapshots of it")
    else:
        rep.gap("what kind of edits people make to the description",
                "no edits to tree.codoc or CLAUDE.md, and no snapshots of either")

    if transcripts:
        rep.ok("what the agent wrote back, and decision survival",
               f"{len(transcripts)} transcript file(s)")
    else:
        rep.gap("what the agent wrote back, and decision survival",
                "no Claude Code transcript. Copy it from ~/.claude/projects before the machine is wiped.")
    rep.manual("who settled each open decision", "your notes and the think-aloud")
    rep.show("Question 1, who writes what")

    # ── question 2: faithfulness and the cost of checking ────────────────────
    rep.rows.clear()
    if projects:
        rep.ok("does the description match the code, and drift",
               "final projects present, rate them blind against the starting archive")
    else:
        rep.gap("does the description match the code, and drift", "no project was collected")

    codoc_state = find(root, "tree.bindings.json")
    if codoc_state:
        rep.ok("proposals raised, accepted, rejected", f"{len(codoc_state)} codoc state snapshots")
    else:
        rep.manual("proposals raised, accepted, rejected",
                   "codoc condition only; none found, which is expected for the other condition")

    views = [e for e in events if e.get("ev") == "view"]
    dwelt = [v for v in views if (v.get("ms") or 0) >= 2000]
    if views:
        rep.ok("review coverage: what they actually looked at",
               f"{len(views)} views, {len(dwelt)} of them two seconds or longer")
    else:
        rep.gap("review coverage: what they actually looked at",
                "no view events, so there is no way to say what was inspected")

    code_views = {v.get("file") for v in views if v.get("surface") in ("code", "test")}
    if views:
        rep.ok("warranted trust: acting without opening the code",
               f"{len(code_views)} code file(s) were on screen at some point")
    else:
        rep.gap("warranted trust: acting without opening the code", "needs view events")
    rep.manual("the sign-off and what it rested on", "your notes, verbatim")
    rep.show("Question 2, faithfulness and the cost of checking")

    # ── question 3 and the task ──────────────────────────────────────────────
    rep.rows.clear()
    rep.manual("the ten questions, closed book then open book", "your notes and the scoring tables")
    if projects:
        rep.ok("the three things being scored, and regressions",
               "run check-hearth.py or check-ember.py on the collected project")
    else:
        rep.gap("the three things being scored, and regressions", "no project was collected")
    rep.show("Question 3, and whether the work got done")

    # ── how they worked ──────────────────────────────────────────────────────
    rep.rows.clear()
    focus = [e for e in events if e.get("ev") == "focus"]
    if edits:
        first = min(e["t"] for e in edits)
        start = min(e["t"] for e in events)
        rep.ok("time to first edit", f"{(first - start) / 1000:.0f}s after the log started")
    else:
        rep.gap("time to first edit", "needs edit events")

    if focus:
        surfaces = [f.get("surface") for f in focus]
        switches = sum(1 for a, b in zip(surfaces, surfaces[1:]) if a != b)
        rep.ok("switches between the description and the code",
               f"{switches} switches across {len(focus)} focus events; "
               f"agent turns come from the transcript")
        opened = [f.get("file") for f in focus]
        rep.ok("how many files they opened before the right one",
               f"{len(dict.fromkeys(opened))} distinct files, in order")
    else:
        rep.gap("switches, and files opened before the right one", "needs focus events")

    if transcripts:
        rep.ok("how long their instructions were", "the transcript")
    else:
        rep.gap("how long their instructions were", "needs the transcript")
    rep.manual("navigation coded into seek, relate and collect",
               "the interaction log with the screen recording")
    rep.show("How they worked")

    # ── the live copy, against the local one ─────────────────────────────────
    rep.rows.clear()
    exports = find(root, "firestore-*.json")
    if not exports:
        rep.manual("the live copy agrees with the local one",
                   "no Firestore export here. Run scripts/export-session.mjs and put the "
                   "file beside this folder, or skip it if the mirror was not used.")
    else:
        live = {}
        try:
            live = json.loads(exports[0].read_text())
            # Said first and loudly. A folder of exports is exactly where a pilot
            # gets analysed by accident, and by the time anybody notices it is
            # already inside a mean.
            if live.get("pilot") or str(live.get("code", "")).startswith("pilot-"):
                print("\n  ** THIS IS A PILOT. It is not part of the analysis. **\n")
        except json.JSONDecodeError:
            rep.gap("the live copy agrees with the local one",
                    f"{exports[0].name} is not readable JSON")

        if live:
            local_counts = Counter()
            for e in events:
                if e.get("ev") in ("edit", "view", "prompt", "agent"):
                    local_counts[e["ev"]] += 1
            live_actions = sum(len(s.get("actions") or [])
                               for s in (live.get("sessions") or {}).values())

            if not live_actions and events:
                rep.gap("the live copy agrees with the local one",
                        "the local log has events and the live copy has none, so the "
                        "mirror never sent anything. The local file is the source of "
                        "truth, so nothing is lost, but say so in the notes.")
            elif live_actions:
                # The mirror maps several raw events into one action and drops
                # what does not map, so these two numbers are not meant to match.
                # What matters is whether a stretch went missing entirely.
                gaps = []
                for condition, s in (live.get("sessions") or {}).items():
                    covered = [c for c in (s.get("covered") or []) if c and c[0] is not None]
                    covered.sort()
                    for (a_from, a_to), (b_from, _) in zip(covered, covered[1:]):
                        if b_from > a_to:
                            gaps.append(f"{condition}: {b_from - a_to} bytes never sent")
                if gaps:
                    rep.gap("the live copy agrees with the local one",
                            "; ".join(gaps) + ". The local log still has it all.")
                else:
                    rep.ok("the live copy agrees with the local one",
                           f"{live_actions} actions arrived, with no gap between batches")

            answers = live.get("answers") or []
            if answers:
                rep.ok("the questionnaires", f"{len(answers)} answer set(s) from the participant page")
            else:
                rep.manual("the questionnaires",
                           "none in the export; if they were filled in on paper, file them yourself")

            assessments = live.get("assessments") or []
            if assessments:
                rep.ok("your notes and scores", f"{len(assessments)} condition(s) recorded")
            else:
                rep.gap("your notes and scores",
                        "no assessment was saved. The sign-off and the question scores are "
                        "not recoverable after the call.")

            blob = json.dumps(live)
            for field in ("\"name\"", "\"email\"", "\"phone\""):
                if field in blob:
                    rep.gap("nothing identifying was stored",
                            f"the export contains {field}, which the rules should have refused")
                    break
            else:
                rep.ok("nothing identifying was stored", "only a code")
    rep.show("The live copy")

    # ── replayability ────────────────────────────────────────────────────────
    rep.rows.clear()
    for proj in projects:
        n = len(git(proj, "log", "--oneline", "--all").splitlines())
        if n > 13:
            rep.ok(f"{proj.name} can be replayed", f"{n} commits including the session snapshots")
        else:
            rep.gap(f"{proj.name} can be replayed",
                    f"only {n} commits, so session-log.sh was not running for it")
    rep.show("Replaying the session")

    print("\nNot visible from here, and still your job:")
    print("  the screen and audio recording, the sign-off, the answers to the")
    print("  ten questions, and the questionnaires.")

    if rep.missing:
        print(f"\n{rep.missing} measure(s) have no data. Fix before the participant leaves.")
        return 1
    print("\nEverything the logs are supposed to carry is here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
