#!/usr/bin/env python3
"""Does the record still work as the agent's memory?

    transfer-probe.py prepare ~/codoc-study/scribe  ~/probes/p07-scribe
    transfer-probe.py run     ~/probes/p07-scribe
    transfer-probe.py score   ~/probes/p07-scribe

Run after all the sessions, not during one. It takes the description a
participant finished with, drops it into a clean copy of the original project as
`CLAUDE.md`, gives an agent a further task, and then checks whether the agent's
change kept the commitments the description was supposed to carry.

Why it is worth the trouble. The strongest objection to the whole project is that
as models improve nobody reads code, so a place to read about code is worth less
over time. The probe concedes the objection and measures the thing anyway,
because a description is also what the agent reads. If a description that a
person kept true produces a better change than one that drifted, the argument
holds in a world where no human ever opens either.

Both conditions hand over the same kind of artifact. The codoc description is
exported to Markdown first, so the agent's access is identical and the only
difference is what the description says.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECTS = HERE.parent / "projects"

# The further task, one per project. Each is a plausible next request whose
# obvious implementation runs across several of the commitments in
# `claims/<project>.json`, so the claims list scores the outcome without needing
# a second rubric.
TASKS = {
    "scribe": ("Add a --keep-notes option that leaves the note markers where they "
               "are in the prose, instead of collecting the notes at the end."),
    "tally": ("Add a --by-account mode that reports each account separately, "
              "beside the existing summary."),
}


def load_scorer():
    """Import `score-record-truth.py`, whose name is not an identifier."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "score_record_truth", HERE / "score-record-truth.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_of(workspace: Path) -> str | None:
    for name in TASKS:
        if (workspace / name).is_dir():
            return name
    return None


def prepare(source: Path, dest: Path) -> int:
    project = project_of(source)
    if project is None:
        print(f"{source} does not look like a scribe or tally workspace", file=sys.stderr)
        return 2
    scorer = load_scorer()
    record, where = scorer.read_record(source)
    if not record.strip():
        print(f"{source} has no description to hand over", file=sys.stderr)
        return 1

    if dest.exists():
        print(f"{dest} already exists; move it aside", file=sys.stderr)
        return 1
    shutil.copytree(PROJECTS / project, dest,
                    ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__",
                                                  "STUDY.md", "ABOUT.md", ".codoc"))
    (dest / "CLAUDE.md").write_text(record)
    (dest / "TASK.txt").write_text(TASKS[project] + "\n")
    (dest / "probe.json").write_text(json.dumps({
        "project": project, "from": str(source), "record_source": where,
        "task": TASKS[project],
    }, indent=2) + "\n")
    print(f"prepared {dest}")
    print(f"  project {project}, description taken from the {where}")
    print(f"  task: {TASKS[project]}")
    return 0


def run(workspace: Path, model: str | None) -> int:
    meta = json.loads((workspace / "probe.json").read_text())
    argv = ["claude", "-p", meta["task"], "--permission-mode", "acceptEdits"]
    if model:
        argv += ["--model", model]
    print(f"running the agent in {workspace}")
    try:
        done = subprocess.run(argv, cwd=workspace, text=True, timeout=1800)
    except FileNotFoundError:
        print("claude is not on PATH; run the task by hand in that folder instead",
              file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("the agent ran past thirty minutes and was stopped", file=sys.stderr)
        return 1
    meta["agent_exit"] = done.returncode
    (workspace / "probe.json").write_text(json.dumps(meta, indent=2) + "\n")
    return 0


def score(workspace: Path) -> int:
    """How many commitments the agent's change kept.

    Only the code half is used here. Whether the agent also updated the
    description is a separate question, and the description it was given is the
    participant's rather than its own.
    """
    meta = json.loads((workspace / "probe.json").read_text())
    scorer = load_scorer()
    spec = json.loads((HERE / "claims" / f"{meta['project']}.json").read_text())

    kept, results = 0, []
    for claim in spec["claims"]:
        probe = claim["probe"]
        output, error = scorer.run_probe(workspace, spec["sample_command"], probe["sample"])
        signal = probe["signal"].encode().decode("unicode_escape")
        present = signal in output
        holds = present if probe["holds_when"] == "present" else not present
        kept += 1 if holds else 0
        results.append({"id": claim["id"], "policy": claim["policy"],
                        "kept": holds, "probe_error": error})
        mark = "kept" if holds else "BROKEN"
        print(f"  {claim['id']}  {mark:7s} {claim['policy']}"
              + (f"   (probe error: {error.splitlines()[0][:60]})" if error else ""))

    meta["kept"] = kept
    meta["total"] = len(results)
    meta["results"] = results
    (workspace / "probe.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"{workspace.name}: {kept} of {len(results)} commitments kept")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("source", type=Path)
    p.add_argument("dest", type=Path)
    r = sub.add_parser("run")
    r.add_argument("workspace", type=Path)
    r.add_argument("--model", default=None)
    s = sub.add_parser("score")
    s.add_argument("workspace", type=Path)

    args = parser.parse_args(argv)
    if args.command == "prepare":
        return prepare(args.source.expanduser().resolve(), args.dest.expanduser())
    if args.command == "run":
        return run(args.workspace.expanduser().resolve(), args.model)
    return score(args.workspace.expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
