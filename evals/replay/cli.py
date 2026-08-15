"""Drive the replay evaluation.

    python -m evals.replay.cli prepare --corpus requests [--depth 200]
    python -m evals.replay.cli run     --corpus requests [--limit 20]
    python -m evals.replay.cli report  [--corpus requests]

``prepare`` clones, rewinds to the start commit and bootstraps the tree.
``run`` walks forward. They are separate because bootstrap costs real money and
is the step most likely to need re-running for reasons that have nothing to do
with the replay.

The reporting corpora refuse to run without ``--i-am-freezing``. That flag is
not a safety rail against mistakes so much as a record of intent: the moment it
is used, Phase 2 has started and the system is supposed to be frozen.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from evals.replay.corpora import by_name, is_reporting, DEV, REPORTING
from evals.replay.gitfacts import _git, checkout, commit_facts, commits_between

WORK = Path(__file__).resolve().parent.parent / "work" / "replay"
OUT = Path(__file__).resolve().parent.parent / "out" / "replay"


def _clone(rc, *, fresh: bool) -> Path:
    dest = WORK / rc.name
    if dest.exists() and not fresh:
        return dest
    if dest.exists():
        import shutil
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"cloning {rc.corpus.source} …")
    # Full history: the start commit is chosen by counting back from HEAD, and a
    # shallow clone silently truncates that count into "as far as we happened to
    # fetch" — which would make the replay depth a property of the network.
    subprocess.run(
        ["git", "clone", rc.corpus.source, str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest


def cmd_prepare(args) -> int:
    rc = by_name(args.corpus)
    depth = args.depth or rc.depth
    repo = _clone(rc, fresh=args.fresh)

    # Pin the tip BEFORE rewinding. `prepare` leaves the repo detached at the
    # start commit, so a later `HEAD` means the start commit and the replay
    # range collapses to nothing.
    head = _git(repo, "rev-parse", "HEAD").strip()
    start = _git(repo, "rev-parse", f"HEAD~{depth}").strip()
    print(f"replaying {start[:10]} → {head[:10]} ({depth} commits)")
    checkout(repo, start)

    codoc_dir = repo / ".codoc"
    if codoc_dir.exists() and not args.fresh:
        print("tree already bootstrapped — pass --fresh to rebuild")
    else:
        if codoc_dir.exists():
            import shutil
            shutil.rmtree(codoc_dir)
        print("bootstrapping (codoc init) — this costs money and takes a while …")
        # NOT cwd=repo. `python -m` puts the working directory on the import
        # path, so bootstrapping a checkout of one of codoc's own dependencies
        # makes that checkout shadow the installed copy: against pydantic,
        # `import pydantic` resolved to the repo under test and died on a
        # version check before codoc ran at all. Running from codoc's own root
        # keeps the import path stable and `--root` still points the work at the
        # target. (The same shadowing would hit a real user who runs `codoc init`
        # inside such a repository — noted in the design doc as its own finding.)
        proc = subprocess.run(
            [sys.executable, "-m", "codoc.cli.main", "init", "--root", str(repo)],
            cwd=str(Path(__file__).resolve().parents[2]), text=True,
        )
        if proc.returncode != 0:
            # Clear the partial workspace. A failed init still leaves `.codoc`
            # (the index is built before the tree), and the "already
            # bootstrapped" branch above then takes a retry straight past the
            # bootstrap. starlette went through the whole frozen run that way:
            # baseline snapshotted at zero features, 168 commits replayed
            # against an empty tree, and every rate came back perfect because
            # there was nothing in it to break.
            import shutil
            shutil.rmtree(codoc_dir, ignore_errors=True)
            print("codoc init failed", file=sys.stderr)
            return 1

    # Refuse to proceed on an empty tree, whatever the exit code said. A
    # workspace with no features cannot exercise anything, and its results look
    # flawless rather than absent.
    from codoc.store.db import open_store
    with open_store(str(codoc_dir)) as _store:
        n_features = len(_store.list_features())
    if n_features == 0:
        print(f"bootstrap produced 0 features in {codoc_dir} — refusing to "
              f"snapshot an empty baseline; re-run with --fresh", file=sys.stderr)
        return 1

    # Snapshot the freshly bootstrapped tree. The replay mutates .codoc forward
    # through history, so this is the only way back to T0 — and getting back to
    # T0 is what makes a scoring fix cost seconds instead of another bootstrap.
    baseline = repo / ".codoc.baseline"
    if not baseline.exists() or args.fresh:
        import shutil
        shutil.rmtree(baseline, ignore_errors=True)
        shutil.copytree(repo / ".codoc", baseline)
        print(f"baseline snapshot → {baseline.name}")

    meta = OUT / rc.name / "meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({
        "corpus": rc.name, "subdir": rc.scope,
        "start": start, "head": head, "depth": depth, "repo": str(repo),
    }, indent=2))
    print(f"ready. meta → {meta}")
    return 0


def cmd_screen(args) -> int:
    """Count what a replay window actually contains, before paying to bootstrap it.

    A repository whose window holds no renames and no deletions cannot exercise
    two of the four mechanical classes, and a run over it would report those as
    "no data" while looking like a full result. Screening is a clone plus a git
    log, so it costs nothing next to a bootstrap.

    **Counting git renames is not enough, and reading this table as if it were
    cost us the relocation arm of a frozen run.** pydantic screened at 231
    renames and was chosen for exactly that; in the run, 149 of its 155 detected
    moves came from renaming a directory of mypy fixture outputs that codoc does
    not index at all, leaving 5 testable relocations. The `sym` column is the one
    to select on: it counts movement of definitions in files codoc would index,
    which is the population the relocation claim is about. `ren` is kept because
    a project with neither is worth skipping outright.
    """
    names = [args.corpus] if args.corpus else [c.name for c in DEV]
    print(f"{'corpus':<12} {'commits':>8} {'indexed':>8} {'add':>6} {'del':>6} "
          f"{'ren':>6} {'mod':>7} {'sym':>6}")
    for name in names:
        rc = by_name(name)
        depth = args.depth or rc.depth
        try:
            repo = _clone(rc, fresh=False)
            head = _git(repo, "rev-parse", "origin/HEAD").strip()
            start = _git(repo, "rev-parse", f"{head}~{depth}").strip()
            shas = commits_between(repo, start, head)
        except Exception as exc:  # noqa: BLE001 — screening must not abort the sweep
            print(f"{name:<12} failed: {str(exc)[:60]}")
            continue
        from evals.replay.symbols import symbol_facts
        a = d = r = m = sym = touched_commits = 0
        for sha in shas:
            f = commit_facts(repo, sha, subdir=rc.scope)
            if f.is_empty:
                continue
            touched_commits += 1
            a += len(f.added); d += len(f.deleted)
            r += len(f.renamed); m += len(f.modified)
            sym += symbol_facts(repo, f.parent, sha, f.touched,
                                renamed=f.renamed).move_count
        print(f"{name:<12} {len(shas):>8} {touched_commits:>8} {a:>6} {d:>6} "
              f"{r:>6} {m:>7} {sym:>6}")
    return 0


def cmd_run(args) -> int:
    rc = by_name(args.corpus)
    if is_reporting(rc.name) and not args.i_am_freezing:
        print(
            f"{rc.name!r} is a REPORTING corpus. Running it starts Phase 2, which "
            f"means the system is frozen and this run's numbers are the ones that "
            f"get published.\nPass --i-am-freezing if that is what you mean.",
            file=sys.stderr,
        )
        return 2

    meta_path = OUT / rc.name / "meta.json"
    if not meta_path.exists():
        print(f"run `prepare --corpus {rc.name}` first", file=sys.stderr)
        return 1
    meta = json.loads(meta_path.read_text())
    repo = Path(meta["repo"])

    # Two arms, two files. The arm is a property of the run, not of the scoring,
    # so mixing them in one file would make every rate a blend of "nobody
    # reviewed" and "someone reviewed" with no way to separate them afterwards.
    arm = args.arm
    out_path = OUT / rc.name / arm / "steps.jsonl"
    trace_path = out_path.with_name("traces.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.fresh:
        for p in (out_path, trace_path):
            p.unlink(missing_ok=True)
        # A replay mutates .codoc forward through history, so restarting needs
        # the tree back at its T0 state. Restoring the post-bootstrap snapshot
        # makes a re-run free; without it every scoring fix costs another
        # `codoc init`, which is the single most expensive step here.
        baseline = repo / ".codoc.baseline"
        if not baseline.exists():
            print("no .codoc.baseline — re-run `prepare --fresh` to make one",
                  file=sys.stderr)
            return 1
        import shutil
        shutil.rmtree(repo / ".codoc", ignore_errors=True)
        shutil.copytree(baseline, repo / ".codoc")
        checkout(repo, meta["start"])
        print("restored the post-bootstrap baseline")

    # Imported here, not at module scope: this pulls in cocoindex, which binds a
    # process-global App to the first codoc_dir it sees. `prepare` and `report`
    # must not pay that, and must not be pinned by it.
    from evals.replay.harness import replay

    done: set[str] = set()
    if out_path.exists():
        done = {json.loads(line)["sha"] for line in out_path.open() if line.strip()}
        print(f"resuming — {len(done)} commits already recorded")

    head = meta.get("head", "HEAD")
    shas = commits_between(repo, meta["start"], head)
    print(f"{len(shas)} commits in range; replaying …")

    records = replay(
        repo, base=meta["start"], head=head, subdir=meta["subdir"],
        out_path=out_path, limit=args.limit, done=done,
        auto_accept=(arm == "attended"),
    )
    errs = sum(1 for r in records if r.error)
    print(f"\n{len(records)} steps recorded, {errs} with errors → {out_path}")
    return 0


def cmd_rescore(args) -> int:
    """Re-apply the scoring rules to saved traces, without replaying anything.

    Ground-truth logic is the part of this harness most likely to still be
    wrong, and re-running a corpus to correct it means paying for a bootstrap
    and hours of Loop A passes to recompute numbers from evidence already on
    disk. The traces hold every input the classifier reads, so a scoring fix is
    a second of CPU.
    """
    from evals.replay.gitfacts import CommitFacts
    from evals.replay.harness import Addr, StepRecord, _classify
    from evals.replay.symbols import symbol_facts

    name = args.corpus
    trace_path = OUT / name / args.arm / "traces.jsonl"
    steps_path = OUT / name / args.arm / "steps.jsonl"
    meta_path = OUT / name / "meta.json"
    repo = Path(json.loads(meta_path.read_text())["repo"]) if meta_path.exists() else None
    if not trace_path.exists():
        print(f"no traces for {name!r} — only runs recorded after traces were "
              f"added can be rescored", file=sys.stderr)
        return 1

    traces = {}
    for line in trace_path.open():
        t = json.loads(line)
        traces[t["sha"]] = t

    rows = [json.loads(line) for line in steps_path.open() if line.strip()]
    rescored, skipped = [], 0
    for row in rows:
        t = traces.get(row["sha"])
        if t is None:
            skipped += 1
            rescored.append(row)
            continue
        parent = _git(repo, "rev-parse", f"{t['sha']}^").strip() if repo else ""
        facts = CommitFacts(
            sha=t["sha"], parent=parent, subject=row.get("subject", ""),
            added=set(t["added"]), deleted=set(t["deleted"]),
            modified=set(t["modified"]), renamed=dict(t["renamed"]),
        )
        # Ground truth is recomputed from git rather than read back from the
        # trace. Deriving it is cheap and it is the part most likely to have
        # been wrong; what the trace exists to preserve is the expensive half,
        # the before/after binding snapshots that only a replay can produce.
        sym = symbol_facts(repo, parent, t["sha"], facts.touched,
                           renamed=facts.renamed)
        unkey = lambda m: {  # noqa: E731
            Addr(*k.split("\t", 1)): v for k, v in m.items()
        }
        rec = StepRecord(sha=t["sha"], parent="", subject="", index=row["index"])
        _classify(facts, sym, unkey(t["before"]), unkey(t["after"]), rec)
        fields = ["followed_rename", "missed_rename", "followed_move",
                  "rebound_move", "lost_move", "detached_on_delete", "stale_after_delete",
                  "symbol_moves"]
        # Traces written before the format carried the full binding map hold
        # only touched files, so the untouched-file invariant cannot be
        # recomputed from them. Those tallies do not depend on the ground-truth
        # logic a rescore revises, so the original run's values are carried
        # through rather than silently overwritten with a structural zero.
        if len(t["before"]) >= t.get("n_before", 0):
            fields += ["survived_untouched", "disturbed_untouched"]
        for f in fields:
            row[f] = getattr(rec, f)
        rescored.append(row)

    if skipped:
        print(f"note: {skipped} step(s) had no trace and kept their original scores")
    out = steps_path.with_name("steps.rescored.jsonl")
    with out.open("w") as fh:
        for row in rescored:
            fh.write(json.dumps(row) + "\n")
    if args.replace:
        out.replace(steps_path)
        print(f"rescored {len(rows) - skipped} step(s) in place")
    else:
        print(f"rescored → {out} (pass --replace to overwrite steps.jsonl)")
    return 0


def _summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    ok = [r for r in rows if not r.get("error")]
    total = lambda k: sum(r.get(k, 0) or 0 for r in ok)  # noqa: E731
    renames = total("followed_rename") + total("missed_rename")
    moves = total("followed_move") + total("rebound_move") + total("lost_move")
    deletes = total("detached_on_delete") + total("stale_after_delete")
    untouched = total("survived_untouched") + total("disturbed_untouched")
    last = ok[-1] if ok else {}
    return {
        "commits": n,
        "errors": n - len(ok),
        "no_llm_pct": round(100 * sum(1 for r in ok if not r.get("llm_called")) / len(ok), 1) if ok else 0,
        "symbol_moves_seen": total("symbol_moves"),
        "move_bound_n": moves,
        "move_survived_pct": round(100 * (moves - total("lost_move")) / moves, 1) if moves else None,
        "move_same_feature_pct": round(100 * total("followed_move") / moves, 1) if moves else None,
        "move_rebound": total("rebound_move"),
        "move_lost": total("lost_move"),
        "rename_followed_pct": round(100 * total("followed_rename") / renames, 1) if renames else None,
        "delete_detached_pct": round(100 * total("detached_on_delete") / deletes, 1) if deletes else None,
        "untouched_undisturbed_pct": round(100 * total("survived_untouched") / untouched, 4) if untouched else None,
        "disturbed_untouched": total("disturbed_untouched"),
        "unresolvable_final": last.get("unresolvable_after"),
        "bindings_final": last.get("bindings_after"),
        "coverage_final": last.get("coverage"),
        "pending_final": last.get("pending_after"),
        "proposals_raised": total("proposed"),
        "tokens": total("input_tokens") + total("output_tokens"),
        "cost_usd": round(total("cost_usd"), 4),
        "seconds": round(total("seconds"), 1),
    }


def cmd_report(args) -> int:
    names = [args.corpus] if args.corpus else [c.name for c in DEV + REPORTING]
    any_found = False
    for name in names:
      for arm in ("unattended", "attended"):
        path = OUT / name / arm / "steps.jsonl"
        if not path.exists():
            continue
        any_found = True
        rows = [json.loads(line) for line in path.open() if line.strip()]
        tag = "REPORTING" if is_reporting(name) else "dev"
        print(f"\n=== {name} · {arm} ({tag}) ===")
        for k, v in _summarize(rows).items():
            print(f"  {k:28s} {v}")
        bad = [r for r in rows if r.get("error")]
        if bad and args.errors:
            print(f"\n  --- {len(bad)} errors ---")
            for r in bad[: args.errors]:
                print(f"  {r['sha'][:8]} {r['subject'][:60]}")
                print("   " + r["error"].strip().splitlines()[-1][:200])
    if not any_found:
        print("no replay output yet", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evals.replay.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("prepare", help="clone, rewind, bootstrap")
    pp.add_argument("--corpus", required=True)
    pp.add_argument("--depth", type=int, default=None)
    pp.add_argument("--fresh", action="store_true")
    pp.set_defaults(fn=cmd_prepare)

    pr = sub.add_parser("run", help="walk the history through Loop A")
    pr.add_argument("--corpus", required=True)
    pr.add_argument("--limit", type=int, default=None)
    pr.add_argument("--fresh", action="store_true")
    pr.add_argument("--i-am-freezing", action="store_true")
    pr.add_argument(
        "--arm", choices=("unattended", "attended"), default="unattended",
        help="unattended: nobody reviews proposals (the system's own claim). "
             "attended: accept every proposal, as a maintainer would.")
    pr.set_defaults(fn=cmd_run)

    sc = sub.add_parser("screen", help="count change classes in a window, no bootstrap")
    sc.add_argument("--corpus", default=None, help="default: every dev corpus")
    sc.add_argument("--depth", type=int, default=None)
    sc.set_defaults(fn=cmd_screen)

    rs = sub.add_parser("rescore", help="re-apply scoring to saved traces, no replay")
    rs.add_argument("--corpus", required=True)
    rs.add_argument("--replace", action="store_true", help="overwrite steps.jsonl")
    rs.add_argument("--arm", choices=("unattended", "attended"), default="unattended")
    rs.set_defaults(fn=cmd_rescore)

    rp = sub.add_parser("report", help="summarize recorded steps")
    rp.add_argument("--corpus", default=None)
    rp.add_argument("--errors", type=int, default=0, help="show N error tracebacks")
    rp.set_defaults(fn=cmd_report)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
