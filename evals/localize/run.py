"""Run the drift experiment: does a maintained document still help, and does a frozen one stop helping?

    python -m evals.localize.run setup  --corpus flask [--hold 50]
    python -m evals.localize.run tasks  --corpus flask
    python -m evals.localize.run eval   --corpus flask [--repeats 3] [--limit 10]
    python -m evals.localize.run report [--corpus flask]

``setup`` is what makes the comparison honest. It copies the prepared workspace,
restores the document to its state at the starting commit, and replays history
forward to a point ``--hold`` commits short of the tip. That leaves two documents
built from the same prose by the same author — one abandoned at the start, one
carried through 200 commits of change — and a block of history that NEITHER has
seen, which is where the tasks come from. Evaluating on commits inside the
replayed range would ask the maintained document to locate a change it had
already been told about.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
from pathlib import Path

from evals.replay.corpora import by_name
from evals.replay.gitfacts import _git, checkout

WORK = Path(__file__).resolve().parent.parent / "work" / "localize"
OUT = Path(__file__).resolve().parent.parent / "out" / "localize"
ARMS = ("none", "frozen", "live")


def _paths(name: str) -> tuple[Path, Path]:
    return WORK / name, OUT / name


def aid_from_store(codoc_dir: Path, *, max_binds: int = 8) -> str:
    """The document as an agent receives it: features, prose, and their addresses.

    NOT the ``tree.codoc`` export. That file is written for a person reading in
    an editor, and it carries almost no inline citations — one, in flask's case
    — because the addresses live in the store and reach an agent through the MCP
    reads instead. Handing over the export would have compared prose against
    prose and called the result a test of maintained addresses.

    This mirrors what ``codoc_tree(include_bindings=True)`` returns: each feature
    with what it is for and the code it owns. Frozen and maintained copies then
    differ in exactly the way the experiment is about, since a stale copy names
    files and symbols the repository has since moved or renamed.
    """
    from codoc.store.db import open_store

    lines: list[str] = []
    with open_store(str(codoc_dir)) as store:
        by_feature = store.bindings_by_feature()
        for f in store.list_features():
            if f.retired:
                continue
            binds = by_feature.get(f.id, [])
            lines.append(f"### {f.title}")
            if f.description:
                lines.append(f.description.strip())
            if binds:
                shown = [b.symbol_path for b in binds[:max_binds]]
                more = f" (+{len(binds) - max_binds} more)" if len(binds) > max_binds else ""
                lines.append("Code: " + ", ".join(shown) + more)
            lines.append("")
    return "\n".join(lines)


def cmd_setup(args) -> int:
    rc = by_name(args.corpus)
    src = Path(__file__).resolve().parent.parent / "work" / "replay" / rc.name
    if not (src / ".codoc.baseline").exists():
        print(f"run `evals.replay.cli prepare --corpus {rc.name}` first", file=sys.stderr)
        return 1
    repo, out = _paths(rc.name)

    if repo.exists() and args.fresh:
        shutil.rmtree(repo)
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        print(f"copying the prepared workspace → {repo}")
        shutil.copytree(src, repo, symlinks=True)

    head = _git(repo, "rev-parse", "origin/HEAD").strip()
    t0 = _git(repo, "rev-parse", f"{head}~{rc.depth}").strip()
    t1 = _git(repo, "rev-parse", f"{head}~{args.hold}").strip()

    out.mkdir(parents=True, exist_ok=True)
    steps_path = out / "replay-steps.jsonl"

    # This replay takes hours on a large project and WILL be interrupted. Restart
    # from the starting document only when there is nothing to resume: otherwise
    # the restore throws away every commit already applied, and the working tree
    # is already past them.
    done: set[str] = set()
    if steps_path.exists() and not args.fresh:
        done = {json.loads(l)["sha"] for l in steps_path.open() if l.strip()}
    if done:
        print(f"resuming — {len(done)} commits already applied")
    else:
        steps_path.unlink(missing_ok=True)
        (out / "traces.jsonl").unlink(missing_ok=True)
        shutil.rmtree(repo / ".codoc", ignore_errors=True)
        shutil.copytree(repo / ".codoc.baseline", repo / ".codoc")
        checkout(repo, t0)

    frozen = aid_from_store(repo / ".codoc.baseline")
    (out / "frozen.md").write_text(frozen)

    from evals.replay.harness import replay
    print(f"replaying {t0[:10]} → {t1[:10]} to build the maintained document …")
    records = replay(repo, base=t0, head=t1, subdir=rc.scope,
                     out_path=steps_path, done=done)
    errs = sum(1 for r in records if r.error)

    # Render the export from the store before snapshotting it. `run_loop_a`
    # updates the store but does not write `tree.codoc` — in production the
    # daemon is that file's sole writer, and the replay harness has no daemon.
    # Without this the "maintained" document is byte-identical to the frozen one
    # and the experiment quietly compares a document against itself.
    (out / "live.md").write_text(aid_from_store(repo / ".codoc"))

    # Both arms answer with the repository as it stands at T1, so the only
    # difference between them is the age of the document they were handed.
    checkout(repo, t1)
    (out / "meta.json").write_text(json.dumps({
        "corpus": rc.name, "repo": str(repo), "t0": t0, "t1": t1, "head": head,
        "replayed": len(records), "replay_errors": errs, "hold": args.hold,
        "frozen_bytes": len(frozen),
        "live_bytes": len((out / "live.md").read_text()),
    }, indent=2))
    print(f"{len(records)} commits replayed ({errs} errors); documents written to {out}")
    return 0


def cmd_tasks(args) -> int:
    rc = by_name(args.corpus)
    repo, out = _paths(rc.name)
    meta = json.loads((out / "meta.json").read_text())
    from evals.localize.tasks import build_tasks, write_tasks

    tasks = build_tasks(repo, start=meta["t1"], end=meta["head"], subdir=rc.scope)
    write_tasks(tasks, out / "tasks.jsonl")
    print(f"{len(tasks)} tasks from the held-out commits → {out / 'tasks.jsonl'}")
    for t in tasks[:6]:
        print(f"  [{t.n_files}f] {t.request[:70]}")
    return 0


def cmd_eval(args) -> int:
    rc = by_name(args.corpus)
    repo, out = _paths(rc.name)
    meta = json.loads((out / "meta.json").read_text())
    from evals.localize.agent import locate, score
    from evals.localize.tasks import read_tasks

    tasks = read_tasks(out / "tasks.jsonl")
    if args.limit:
        tasks = tasks[: args.limit]
    aids = {
        "none": "",
        "frozen": (out / "frozen.md").read_text(),
        "live": (out / "live.md").read_text(),
    }
    checkout(repo, meta["t1"])

    results_path = out / "results.jsonl"
    done = set()
    if results_path.exists() and not args.fresh:
        done = {(r["sha"], r["arm"], r["run"])
                for r in (json.loads(l) for l in results_path.open() if l.strip())}
    elif results_path.exists():
        results_path.unlink()

    with results_path.open("a") as sink:
        for run in range(args.repeats):
            for task in tasks:
                for arm in ARMS:
                    if (task.sha, arm, run) in done:
                        continue
                    trace = locate(repo, task.request, aids[arm])
                    row = {
                        "sha": task.sha, "arm": arm, "run": run,
                        "request": task.request, "gold": task.gold_files,
                        "predicted": trace.predicted, "steps": trace.steps,
                        "tools": trace.tool_calls, "error": trace.error,
                        "tokens": trace.input_tokens + trace.output_tokens,
                        **score(trace.predicted, task.gold_files),
                    }
                    sink.write(json.dumps(row) + "\n")
                    sink.flush()
            print(f"run {run + 1}/{args.repeats} done")
    print(f"→ {results_path}")
    return 0


def cmd_report(args) -> int:
    names = [args.corpus] if args.corpus else [p.name for p in OUT.iterdir()
                                               if (p / "results.jsonl").exists()]
    for name in names:
        path = OUT / name / "results.jsonl"
        if not path.exists():
            continue
        rows = [json.loads(l) for l in path.open() if l.strip()]
        meta = json.loads((OUT / name / "meta.json").read_text())
        print(f"\n=== {name} · documents {meta['hold']} commits apart in age ===")
        print(f"{'arm':<8} {'n':>4} {'steps':>7} {'F1':>7} {'recall':>7} "
              f"{'found':>7} {'exact':>7} {'tokens':>8}")
        for arm in ARMS:
            r = [x for x in rows if x["arm"] == arm and not x["error"]]
            if not r:
                continue
            med = lambda k: round(statistics.median(x[k] for x in r), 2)  # noqa: E731
            avg = lambda k: round(sum(x[k] for x in r) / len(r), 3)       # noqa: E731
            print(f"{arm:<8} {len(r):>4} {med('steps'):>7} {avg('f1'):>7} "
                  f"{avg('recall'):>7} {avg('found_any'):>7} {avg('exact'):>7} "
                  f"{round(sum(x['tokens'] for x in r) / len(r)):>8}")
        bad = [x for x in rows if x["error"]]
        if bad:
            print(f"  ({len(bad)} errored)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evals.localize.run")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="build the frozen and maintained documents")
    s.add_argument("--corpus", required=True)
    s.add_argument("--hold", type=int, default=50,
                   help="commits held out for tasks; no arm sees these")
    s.add_argument("--fresh", action="store_true")
    s.set_defaults(fn=cmd_setup)

    t = sub.add_parser("tasks", help="derive tasks from the held-out commits")
    t.add_argument("--corpus", required=True)
    t.set_defaults(fn=cmd_tasks)

    e = sub.add_parser("eval", help="run every arm over every task")
    e.add_argument("--corpus", required=True)
    e.add_argument("--repeats", type=int, default=3)
    e.add_argument("--limit", type=int, default=None)
    e.add_argument("--fresh", action="store_true")
    e.set_defaults(fn=cmd_eval)

    r = sub.add_parser("report", help="summarize")
    r.add_argument("--corpus", default=None)
    r.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
