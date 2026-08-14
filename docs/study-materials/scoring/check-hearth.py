#!/usr/bin/env python3
"""Score one finished hearth workspace against the three hazards.

Run it after a session, against the participant's workspace:

    python3 check-hearth.py ~/codoc-study/hearth --adapter p04.json

The assertions are fixed and are frozen at pre-registration. What is not fixed is
how a participant chose to drive their own implementation, because the task card
leaves that open on purpose. So the driving details come from a small adapter
file you write per participant, after reading their code, e.g.:

    {
      "draft_marker": {"kind": "frontmatter", "key": "draft", "value": "true"},
      "prod_build":   ".venv/bin/hearth build",
      "dev_build":    ".venv/bin/hearth build --drafts"
    }

    {
      "draft_marker": {"kind": "folder", "path": "content/_drafts"},
      "prod_build":   ".venv/bin/hearth build",
      "dev_build":    "HEARTH_ENV=dev .venv/bin/hearth build"
    }

Writing the adapter is a judgment call about what the participant built. Scoring
is not. Record the adapter alongside the result, so the scoring is reproducible.
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

AGGREGATES = ["index.html", "feed.xml", "sitemap.xml"]


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool | None, str]] = []

    def add(self, name: str, passed: bool | None, detail: str = "") -> None:
        self.rows.append((name, passed, detail))

    def report(self) -> int:
        print()
        worst = 0
        for name, passed, detail in self.rows:
            if passed is None:
                mark, worst = "SKIP", max(worst, 1)
            elif passed:
                mark = "PASS"
            else:
                mark, worst = "FAIL", 2
            print(f"  {mark}  {name}")
            if detail:
                print(f"        {detail}")
        print()
        return worst


def run(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)


def pick_post(ws: Path) -> Path:
    """The post we will flip. Always the same one, so runs are comparable."""
    target = ws / "content" / "posts" / "a-winter-stock.md"
    if target.exists():
        return target
    posts = sorted((ws / "content" / "posts").glob("*.md"))
    if not posts:
        sys.exit("no posts found under content/posts/")
    return posts[0]


def mark_draft(post: Path, marker: dict, ws: Path) -> Path:
    """Mark the post as a draft. Returns its new path."""
    kind = marker.get("kind")
    if kind == "frontmatter":
        key, value = marker["key"], marker.get("value", "true")
        text = post.read_text()
        if not text.startswith("---"):
            sys.exit(f"{post} has no frontmatter block")
        end = text.index("\n---", 3)
        post.write_text(f"{text[:end]}\n{key}: {value}{text[end:]}")
        return post
    if kind == "folder":
        dest_dir = ws / marker["path"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / post.name
        shutil.move(str(post), str(dest))
        return dest
    sys.exit(f"unknown draft_marker kind: {kind!r}")


def snapshot_content(ws: Path) -> Path:
    """Copy content/ aside so we can put it back exactly as the participant left it.

    We deliberately do not use git here. A participant may never have committed,
    and `git checkout` would then delete the work we are trying to score.
    """
    keep = Path(tempfile.mkdtemp(prefix="check-hearth-"))
    shutil.copytree(ws / "content", keep / "content", symlinks=True)
    return keep


def restore(ws: Path, keep: Path) -> None:
    """Put the sample content and the build state back the way we found them."""
    shutil.rmtree(ws / "content", ignore_errors=True)
    shutil.copytree(keep / "content", ws / "content", symlinks=True)
    shutil.rmtree(ws / "_site", ignore_errors=True)
    shutil.rmtree(ws / ".hearth", ignore_errors=True)


def site_mentions(ws: Path, slug: str, files: list[str]) -> list[str]:
    """Which of the given output files still mention the post."""
    hits = []
    for name in files:
        f = ws / "_site" / name
        if f.exists() and re.search(re.escape(slug), f.read_text(errors="ignore")):
            hits.append(name)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--keep", action="store_true",
                    help="do not restore the workspace afterwards")
    args = ap.parse_args()

    ws = args.workspace.expanduser().resolve()
    cfg = json.loads(args.adapter.read_text())
    prod = cfg.get("prod_build", ".venv/bin/hearth build")
    dev = cfg.get("dev_build")
    res = Result()

    keep = snapshot_content(ws)
    post = pick_post(ws)
    slug = post.stem
    print(f"workspace: {ws}")
    print(f"post used: {post.relative_to(ws)}  (slug '{slug}')")

    try:
        # 1. Clean published build. The post must be listed, or the rest is meaningless.
        shutil.rmtree(ws / "_site", ignore_errors=True)
        shutil.rmtree(ws / ".hearth", ignore_errors=True)
        first = run(prod, ws)
        if first.returncode != 0:
            res.add("the project builds", False, first.stderr.strip()[:300] or first.stdout.strip()[:300])
            return res.report()
        res.add("the project builds", True, first.stdout.strip().splitlines()[-1] if first.stdout.strip() else "")

        listed = site_mentions(ws, slug, AGGREGATES)
        if not listed:
            res.add("the post is published before we flip it", False,
                    "it is not on the home page, feed, or sitemap, so the workspace is not in a clean starting state")
            return res.report()
        res.add("the post is published before we flip it", True, f"listed in {', '.join(listed)}")

        # 2. H1. Flip to draft and rebuild INCREMENTALLY, with the cache still warm.
        #    A filter applied downstream of collection assembly never reaches the
        #    aggregate signature, so the listing pages stay stale.
        post = mark_draft(post, cfg["draft_marker"], ws)
        inc = run(prod, ws)
        stale = site_mentions(ws, slug, AGGREGATES)
        adapter_ok = True
        if not stale:
            res.add("The hidden rule [H1]: the summary pages update after a post is made a draft",
                    True,
                    f"gone from all of {', '.join(AGGREGATES)} "
                    f"({inc.stdout.strip().splitlines()[-1] if inc.stdout.strip() else ''})")
        else:
            # The post is still listed, which means either the hazard fired or we
            # marked it in a way this implementation does not read. Those look
            # identical here, so tell them apart: throw the cache and the output
            # away and build from scratch. A marker the code understands must take
            # effect then.
            shutil.rmtree(ws / "_site", ignore_errors=True)
            shutil.rmtree(ws / ".hearth", ignore_errors=True)
            clean = run(prod, ws)
            listed_now = site_mentions(ws, slug, AGGREGATES)
            built = (ws / "_site" / "index.html").exists()
            bad_adapter = "your settings file matches how this code marks a draft"
            if clean.returncode != 0:
                why = (clean.stderr.strip() or clean.stdout.strip() or "").splitlines()
                res.add(bad_adapter, False,
                        "the project stops building once your marker is applied: "
                        + (why[-1][:200] if why else f"exit {clean.returncode}")
                        + "  |  fix your settings file and score again. The hidden rule was NOT measured.")
                adapter_ok = False
            elif not built:
                res.add(bad_adapter, False,
                        "a build from scratch wrote no home page at all, so there is nothing "
                        "to judge. Fix your settings file and score again. The hidden rule was NOT measured.")
                adapter_ok = False
            elif listed_now:
                res.add(bad_adapter, False,
                        "a build from scratch still lists the post, so nothing was marked as "
                        "a draft at all. Read their code and fix your settings file, then "
                        "score again. The hidden rule was NOT measured.")
                adapter_ok = False
            else:
                res.add(bad_adapter, True, "a build from scratch drops the post")
                res.add("The hidden rule [H1]: the summary pages update after a post is made a draft",
                        False,
                        f"still listed in {', '.join(stale)} after an incremental build, and "
                        f"only a build from scratch cleared them")

        # 3 and 4. Skip the rest when the marker never marked anything, because
        #          those results would describe our mistake and not their code.
        if not adapter_ok:
            res.add("the draft's own page is taken out of the published site", None,
                    "not measured")
            res.add("The stated requirement [H3]: the preview still shows the draft", None, "not measured")
        else:
          # The depth rung. The draft's own page must not be left on disk.
          own = list((ws / "_site").rglob(f"*{slug}*"))
          res.add("the draft's own page is taken out of the published site",
                  not own,
                  f"still served at {own[0].relative_to(ws)}" if own else "")

          # H3. A dev build must show the draft again.
          if not dev:
            res.add("The stated requirement [H3]: the preview still shows the draft", None,
                    "no dev_build in the adapter, so this was not checked")
          else:
            shutil.rmtree(ws / "_site", ignore_errors=True)
            shutil.rmtree(ws / ".hearth", ignore_errors=True)
            d = run(dev, ws)
            shown = site_mentions(ws, slug, AGGREGATES)
            res.add("The stated requirement [H3]: the preview still shows the draft", bool(shown),
                    f"listed in {', '.join(shown)}" if shown
                    else (d.stderr.strip()[:300] or "the draft does not appear anywhere in the dev build"))

        # 5. No regressions. Put the sample content back first. The checks above
        #    leave a post marked as a draft, and the project's own tests assert on
        #    the sample site, so running them now would fail for our reason rather
        #    than the participant's.
        restore(ws, keep)
        t = run(".venv/bin/python -m pytest tests/ -q", ws)
        last = (t.stdout.strip().splitlines() or [""])[-1]
        res.add("the existing tests still pass", t.returncode == 0, last)

    finally:
        if args.keep:
            print(f"left the workspace as it is. Your content backup is at {keep}")
        else:
            restore(ws, keep)
            shutil.rmtree(keep, ignore_errors=True)

    return res.report()


if __name__ == "__main__":
    sys.exit(main())
