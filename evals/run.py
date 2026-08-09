"""Run the N1/N2 comparison end to end.

    python -m evals.run prepare  --corpus requests      # clone + build both arms
    python -m evals.run items    --corpus requests      # generate + filter questions
    python -m evals.run score    --corpus requests      # read + judge both arms
    python -m evals.run report                          # summary across corpora

Four commands rather than one, because the stages have very different costs and
failure modes: cloning is free and repeatable, artifact building spends real
money and takes minutes, item generation spends more and is the stage whose
output deserves reading before anything is scored against it. Fusing them would
mean a bad question set is discovered only after paying to score it twice.

Every stage writes to ``evals/out/<corpus>/`` and is resumable: a stage that
already has its output skips unless ``--fresh`` is passed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evals import arms, corpora, items as items_mod, score as score_mod

OUT = Path(__file__).resolve().parent / "out"
WORK = Path(__file__).resolve().parent / "work"


def _paths(name: str) -> dict[str, Path]:
    base = OUT / name
    return {
        "base": base,
        "artifacts": base / "artifacts.json",
        "items": base / "items.jsonl",
        "results": base / "results.jsonl",
        "summary": base / "summary.json",
        "sample": base / "human_sample.jsonl",
    }


def cmd_prepare(args) -> int:
    corpus = corpora.by_name(args.corpus)
    p = _paths(corpus.name)
    if p["artifacts"].exists() and not args.fresh:
        print(f"artifacts already built for {corpus.name} — pass --fresh to rebuild")
        return 0
    checkout = corpora.materialize(corpus, WORK, fresh=args.fresh)
    depth = corpora.history_depth(checkout)
    print(f"{corpus.name}: {checkout} ({depth} commits)")
    if depth < 50:
        print("  ⚠ shallow history — N2 ground truth will be thin", file=sys.stderr)

    built = arms.build_all(checkout, subdir=corpus.subdir, max_files=args.max_files)
    p["base"].mkdir(parents=True, exist_ok=True)
    payload = {}
    for a in built:
        status = "ok" if a.ok else f"FAILED: {a.detail}"
        print(f"  {a.arm}: {status}" + (f" ({len(a.text)} chars)" if a.ok else ""))
        payload[a.arm] = {"ok": a.ok, "path": str(a.path), "detail": a.detail,
                          "text": a.text}
    p["artifacts"].write_text(json.dumps(payload), encoding="utf-8")
    if not all(a.ok for a in built):
        print("  ⚠ an arm failed to build — scoring it now would report a tool "
              "failure as a method result", file=sys.stderr)
        return 1
    return 0


def cmd_items(args) -> int:
    corpus = corpora.by_name(args.corpus)
    p = _paths(corpus.name)
    if p["items"].exists() and not args.fresh:
        print(f"items already generated ({len(items_mod.read_items(p['items']))}) "
              "— pass --fresh to regenerate")
        return 0
    checkout = WORK / corpus.name
    if not checkout.exists():
        print(f"no checkout — run `prepare --corpus {corpus.name}` first", file=sys.stderr)
        return 1

    generated: list[items_mod.Item] = []
    if "n2" in args.measures:
        commits = items_mod.reasoned_commits(checkout, corpus.subdir, limit=args.n2)
        print(f"{corpus.name}: {len(commits)} commits state a reason")
        n2 = items_mod.make_n2_items(checkout, corpus.name, commits)
        kept = len(n2)
        print(f"  n2: {kept} items kept of {len(commits)} candidates "
              f"({len(commits) - kept} answerable from code alone, or stated no reason)")
        generated += n2
    if "n1" in args.measures:
        files = items_mod.source_files(checkout, corpus.subdir)
        n1 = items_mod.make_n1_items(checkout, corpus.name, files, n=args.n1)
        print(f"  n1: {len(n1)} items")
        generated += n1

    items_mod.write_items(generated, p["items"])
    print(f"  → {p['items']}")
    print("  read them before scoring — a question set nobody looked at is not "
          "ground truth")
    return 0


def cmd_score(args) -> int:
    corpus = corpora.by_name(args.corpus)
    p = _paths(corpus.name)
    if not p["items"].exists() or not p["artifacts"].exists():
        print("run `prepare` and `items` first", file=sys.stderr)
        return 1
    artifacts = json.loads(p["artifacts"].read_text(encoding="utf-8"))
    item_list = items_mod.read_items(p["items"])

    # A capped codoc arm describes part of the package while the baseline read
    # all of it. Scoring that reports a harness setting as a method result — the
    # first such run produced a two-file tree and looked like a total loss.
    capped = [a for a, v in artifacts.items() if "capped" in (v.get("detail") or "")]
    if capped and not args.allow_partial:
        print(f"refusing to score: {', '.join(capped)} was built partially "
              f"({artifacts[capped[0]]['detail']}) while the other arm saw everything.\n"
              f"  rebuild without --max-files, or pass --allow-partial to score anyway",
              file=sys.stderr)
        return 1

    results: list[score_mod.Result] = []
    for arm, payload in sorted(artifacts.items()):
        if not payload.get("ok"):
            print(f"  skipping {arm}: {payload.get('detail')}", file=sys.stderr)
            continue
        for i, item in enumerate(item_list, 1):
            results.append(score_mod.answer_and_score(item, arm, payload["text"]))
            if i % 10 == 0:
                print(f"  {arm}: {i}/{len(item_list)}")

    score_mod.write_results(results, p["results"])
    summary = score_mod.summarize(results)
    p["summary"].write_text(json.dumps(summary, indent=2), encoding="utf-8")
    score_mod.write_results(score_mod.human_sample(results), p["sample"])
    print(json.dumps(summary, indent=2))
    print(f"  → {p['results']}\n  → hand-score {p['sample']}")
    return 0


def cmd_report(args) -> int:
    rows: list[dict] = []
    for corpus in corpora.CORPORA:
        p = _paths(corpus.name)
        if not p["summary"].exists():
            continue
        for cell, stats in json.loads(p["summary"].read_text()).items():
            rows.append({"cell": cell, "arm_kind": corpus.arm, **stats})
    if not rows:
        print("nothing scored yet")
        return 0
    print(f"{'cell':<38} {'n':>4} {'mean':>6} {'full':>6} {'absent':>7} {'copied':>7}")
    for r in sorted(rows, key=lambda r: r["cell"]):
        print(f"{r['cell']:<38} {r['n']:>4} {r['mean_score']:>6.2f} "
              f"{r['full_credit']:>6.2f} {r['not_in_document']:>7.2f} "
              f"{r['mean_verbatim_overlap']:>7.2f}")
    print("\nthird-party corpora are the result; the codenav arm is a ceiling and "
          "is reported separately, never pooled.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    prep = sub.add_parser("prepare", help="clone the corpus and build both arms")
    prep.add_argument("--corpus", required=True)
    prep.add_argument("--fresh", action="store_true")
    prep.add_argument("--max-files", type=int, default=None,
                      help="cap codoc bootstrap files (recorded on the artifact)")
    prep.set_defaults(fn=cmd_prepare)

    it = sub.add_parser("items", help="generate and filter questions")
    it.add_argument("--corpus", required=True)
    it.add_argument("--fresh", action="store_true")
    it.add_argument("--measures", default="n1,n2")
    it.add_argument("--n1", type=int, default=12)
    it.add_argument("--n2", type=int, default=40)
    it.set_defaults(fn=cmd_items)

    sc = sub.add_parser("score", help="read and judge both arms")
    sc.add_argument("--corpus", required=True)
    sc.add_argument("--allow-partial", action="store_true",
                    help="score even when an arm was built from part of the corpus")
    sc.set_defaults(fn=cmd_score)

    rp = sub.add_parser("report", help="summary across corpora")
    rp.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    if getattr(args, "measures", None):
        args.measures = [m.strip() for m in args.measures.split(",")]
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
