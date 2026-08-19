#!/usr/bin/env python3
"""Is the record still true at the end of the session?

    python3 score-record-truth.py ~/codoc-study/scribe
    python3 score-record-truth.py <collected-folder>/tally --out sheets/

The headline outcome of the review task. Each project has a short list of claims
its description makes, in `claims/<project>.json`. For each claim this runs the
participant's FINAL code on a sample and looks for one signal, so what the code
does is measured rather than assumed, and then pulls the sentences from the
participant's final description that talk about the same policy.

What comes out is a rating sheet. A rater marks each claim true, contradicted, or
missing, without being told which condition the session was in. The sheet does not
name the condition and does not say which description it came from, because the
codoc description is exported to Markdown first and reads like any other.

The code half is automatic and the record half is not. A claim can be worded ten
ways and still be true, and matching words to behaviour is the judgement the
measure is about, so the tool does the part a person would get wrong by hand and
leaves the part a person is better at.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAIMS = HERE / "claims"
SAMPLES = CLAIMS / "samples"


def python_for(workspace: Path) -> str:
    venv = workspace / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def run_probe(workspace: Path, command: list[str], sample: str,
              reads: str = "") -> tuple[str, str]:
    """Run the project on a sample and return what the probe should look at.

    The sample is copied into a scratch directory first, because some of these
    programs write their output beside their input and a probe must not leave
    files in the repository it is checking.

    `reads` names a file the program writes, relative to the copied sample, for a
    probe about something the program puts in a file rather than on the screen.
    Without it the probe looks at standard output. A probe that reads a written
    file usually needs its own command too, because the command that prints to the
    screen is often the one that writes nothing.
    """
    scratch = Path(tempfile.mkdtemp(prefix="probe-"))
    try:
        local = scratch / Path(sample).name
        shutil.copy2(SAMPLES / sample, local)
        argv = [python_for(workspace) if part == "python" else part for part in command]
        argv = [part.replace("{sample}", str(local)) for part in argv]
        try:
            done = subprocess.run(argv, cwd=workspace, capture_output=True,
                                  text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as error:
            return "", f"{type(error).__name__}: {error}"
        error = done.stderr.strip() if done.returncode else ""
        if not reads:
            return done.stdout, error
        wanted = scratch / reads
        if not wanted.exists():
            return "", error or f"the program wrote no {reads}"
        return wanted.read_text(errors="replace"), error
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def read_record(workspace: Path) -> tuple[str, str]:
    """The participant's final description, and where it came from.

    The codoc condition keeps its description in a store rather than a file, so
    it is exported to Markdown. Both conditions then read as ordinary prose and
    the rater cannot tell them apart, which is what blind rating needs.
    """
    claude_md = workspace / "CLAUDE.md"
    if (workspace / ".codoc").is_dir():
        try:
            done = subprocess.run(["codoc", "export-markdown", "--stdout"],
                                  cwd=workspace, capture_output=True, text=True, timeout=120)
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout, "exported description"
        except (OSError, subprocess.TimeoutExpired):
            pass
        tree = workspace / ".codoc" / "tree.codoc"
        if tree.exists():
            return tree.read_text(errors="replace"), "exported description"
    if claude_md.exists():
        return claude_md.read_text(errors="replace"), "written description"
    return "", "no description found"


def is_prose(chunk: str) -> bool:
    """Whether a chunk is a sentence rather than a list of code it points at.

    Both descriptions carry the symbols each part of the program owns, written as
    a `Code:` line of backticked paths. A list of test names mentions almost every
    policy word there is, so left in it outscores the sentence that makes the
    claim and the rater is handed the wrong text.
    """
    if chunk.lstrip().startswith(("Code:", "`")):
        return False
    return chunk.count("`") <= 4


def candidates(record: str, keywords: list[str], limit: int = 3) -> list[str]:
    """The sentences in the record most likely to be making this claim."""
    chunks = [c.strip() for c in re.split(r"(?<=[.!?])\s+|\n{2,}", record) if c.strip()]
    scored = []
    for chunk in chunks:
        if not is_prose(chunk):
            continue
        lowered = chunk.lower()
        hits = sum(1 for word in keywords if word.lower() in lowered)
        if hits:
            scored.append((hits, -len(chunk), chunk))
    scored.sort(reverse=True)
    return [chunk for _, _, chunk in scored[:limit]]


def score(workspace: Path, out_dir: Path | None) -> int:
    project = None
    for name in ("scribe", "tally"):
        if (workspace / name).is_dir():
            project = name
    if project is None:
        print(f"{workspace} does not look like a scribe or tally workspace", file=sys.stderr)
        return 2

    spec = json.loads((CLAIMS / f"{project}.json").read_text())
    record, source = read_record(workspace)
    rows = []
    for claim in spec["claims"]:
        probe = claim["probe"]
        output, error = run_probe(workspace, probe.get("command") or spec["sample_command"],
                                  probe["sample"], probe.get("reads", ""))
        signal = probe["signal"].encode().decode("unicode_escape")
        present = signal in output
        holds = present if probe["holds_when"] == "present" else not present
        rows.append({
            "id": claim["id"],
            "policy": claim["policy"],
            "planted": claim.get("planted", ""),
            "statement": claim["statement"],
            "code_does": probe["present_means"] if present else probe["absent_means"],
            "code_still_matches_the_claim": holds,
            "probe_error": error,
            "record_says": candidates(record, claim["find_in_record"]),
            "rating": "",
        })

    sheet = {"project": project, "workspace": workspace.name, "record_source": source,
             "claims": rows}
    out_dir = out_dir or workspace.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"record-truth-{workspace.name}"
    (out_dir / f"{stem}.json").write_text(json.dumps(sheet, indent=2) + "\n")
    (out_dir / f"{stem}.md").write_text(as_sheet(sheet))

    broken = [r for r in rows if r["probe_error"]]
    kept = sum(1 for r in rows if r["code_still_matches_the_claim"])
    print(f"{workspace.name}: {kept} of {len(rows)} claims still match the code"
          + (f", {len(broken)} probe(s) failed to run" if broken else ""))
    for row in broken:
        print(f"  {row['id']}: {row['probe_error'].splitlines()[0][:120]}")
    print(f"  {out_dir / f'{stem}.md'}")
    return 0


def as_sheet(sheet: dict) -> str:
    lines = [f"# Is the record true? {sheet['project']}", "",
             "Mark each claim **true**, **contradicted**, or **missing**, by reading",
             "what the description says against what the code does. The condition is",
             "not recorded here on purpose.", ""]
    for row in sheet["claims"]:
        lines += [f"## {row['id']}. {row['policy']}", "",
                  f"The description is supposed to say: {row['statement']}", "",
                  f"What the code does: {row['code_does']}.", ""]
        if row["probe_error"]:
            lines += [f"The probe did not run: `{row['probe_error'].splitlines()[0]}`", ""]
        if row["record_says"]:
            lines.append("What this description says:")
            lines += [f"> {c}" for c in row["record_says"]]
        else:
            lines.append("This description says nothing that mentions the policy.")
        lines += ["", "Rating: ", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    return score(args.workspace.expanduser().resolve(), args.out)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
