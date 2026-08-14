#!/usr/bin/env python3
"""Score one finished ember workspace against the three hazards.

    python3 check-ember.py ~/codoc-study/ember --adapter p04-ember.json

The sibling of `check-hearth.py`, which does the same job for hearth. The
assertions are fixed and are frozen at pre-registration. How a participant chose
to drive their own implementation is not fixed, because the task card leaves it
open, so that part comes from a small adapter file you write per participant
after reading their code:

    {
      "mute_marker": {"kind": "feeds_toml", "key": "muted", "value": "true"},
      "refresh":     ".venv/bin/python -m ember refresh",
      "digest":      ".venv/bin/python -m ember digest",
      "archive":     ".venv/bin/python -m ember archive"
    }

Use `{"kind": "config", "file": "ember.toml", "line": "muted = [\\"saltbox-kitchen\\"]"}`
when they put the mute list in the config instead of on the feed.

The three hazards:
  H1  muting a feed must reach the digest signature, so the day pages are
      rewritten. A filter in the renderer leaves them serving the muted feed.
  H2  whether muted items still count in the notification log and the status
      counts is a judgment call, scored by hand, not here.
  H3  the archive and the search corpus must KEEP the muted feed's items,
      because muting hides a feed from the digest and does not delete it.
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

FEED = "saltbox-kitchen"
DIGEST_DIR = "_digest"
ARCHIVE_FILES = ["archive/search.json", "archive/index.html"]


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


def snapshot(ws: Path) -> Path:
    """Copy the config files aside. We never use git, because a participant may
    not have committed and `git checkout` would delete the work being scored."""
    keep = Path(tempfile.mkdtemp(prefix="hazard-ember-"))
    for name in ("feeds.toml", "ember.toml"):
        if (ws / name).exists():
            shutil.copy2(ws / name, keep / name)
    return keep


def restore(ws: Path, keep: Path) -> None:
    for name in ("feeds.toml", "ember.toml"):
        if (keep / name).exists():
            shutil.copy2(keep / name, ws / name)
    shutil.rmtree(ws / DIGEST_DIR, ignore_errors=True)
    shutil.rmtree(ws / ".ember", ignore_errors=True)


def apply_mute(ws: Path, marker: dict) -> None:
    kind = marker.get("kind")
    if kind == "feeds_toml":
        key, value = marker["key"], marker.get("value", "true")
        p = ws / "feeds.toml"
        text = p.read_text()
        anchor = f'name = "{FEED}"'
        if anchor not in text:
            sys.exit(f"feeds.toml has no feed called {FEED}")
        lines, out, inside = text.splitlines(), [], False
        for line in lines:
            out.append(line)
            if line.strip() == anchor:
                inside = True
            elif inside and (not line.strip() or line.strip().startswith("[")):
                out.insert(len(out) - 1, f"{key} = {value}")
                inside = False
        if inside:
            out.append(f"{key} = {value}")
        p.write_text("\n".join(out) + "\n")
        return
    if kind == "config":
        p = ws / marker.get("file", "ember.toml")
        p.write_text(p.read_text().rstrip() + "\n" + marker["line"] + "\n")
        return
    sys.exit(f"unknown mute_marker kind: {kind!r}")


def mentions(ws: Path, pattern: str, files: list[Path]) -> list[str]:
    hits = []
    for f in files:
        if f.exists() and re.search(re.escape(pattern), f.read_text(errors="ignore")):
            hits.append(str(f.relative_to(ws)))
    return hits


def digest_pages(ws: Path) -> list[Path]:
    d = ws / DIGEST_DIR
    return sorted(p for p in d.glob("*.html")) if d.exists() else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    ws = args.workspace.expanduser().resolve()
    cfg = json.loads(args.adapter.read_text())
    refresh = cfg.get("refresh", ".venv/bin/python -m ember refresh")
    digest = cfg.get("digest", ".venv/bin/python -m ember digest")
    archive = cfg.get("archive", ".venv/bin/python -m ember archive")
    res = Result()
    adapter_ok = True
    keep = snapshot(ws)
    print(f"workspace: {ws}")
    print(f"feed used: {FEED}")

    try:
        # 1. A clean run with nothing muted. The feed must be in the digest, or
        #    nothing below means anything.
        shutil.rmtree(ws / DIGEST_DIR, ignore_errors=True)
        shutil.rmtree(ws / ".ember", ignore_errors=True)
        r = run(refresh, ws)
        if r.returncode != 0:
            res.add("the project runs", False, (r.stderr or r.stdout).strip()[:300])
            return res.report()
        d = run(digest, ws)
        run(archive, ws)
        if d.returncode != 0:
            res.add("the project runs", False, (d.stderr or d.stdout).strip()[:300])
            return res.report()
        res.add("the project runs", True, (d.stdout.strip().splitlines() or [""])[-1])

        shown = mentions(ws, FEED, digest_pages(ws))
        if not shown:
            res.add("the feed is in the digest before we mute it", False,
                    "it is on no digest page, so the workspace is not in a clean starting state")
            return res.report()
        res.add("the feed is in the digest before we mute it", True,
                f"on {len(shown)} digest pages")

        # 2. H1. Mute the feed, then run the digest again with the state intact.
        #    A filter downstream of the signature never reaches it, so the run
        #    reports nothing to write and the pages keep the muted feed.
        apply_mute(ws, cfg["mute_marker"])
        inc = run(digest, ws)
        if inc.returncode != 0:
            res.add("The hidden rule [H1]: the digest is rewritten after a feed is muted", False,
                    (inc.stderr or inc.stdout).strip()[:300])
        else:
            stale = mentions(ws, FEED, digest_pages(ws))
            if not stale:
                res.add("The hidden rule [H1]: the digest is rewritten after a feed is muted", True,
                        (inc.stdout.strip().splitlines() or [""])[-1])
            else:
                # The feed is still there, which means either the hazard fired or we
                # muted it in a way this implementation does not read. Those look
                # identical here, so tell them apart: throw the state away and build
                # from scratch. A mute the code understands must take effect then.
                shutil.rmtree(ws / DIGEST_DIR, ignore_errors=True)
                shutil.rmtree(ws / ".ember", ignore_errors=True)
                r2 = run(refresh, ws)
                d2 = run(digest, ws)
                pages = digest_pages(ws)
                bad_adapter = (
                    "your settings file matches how this code mutes a feed")
                if r2.returncode != 0 or d2.returncode != 0:
                    # Most likely the marker is a key the config rejects, e.g. a
                    # per-feed flag on an implementation that reads a config list.
                    failed = d2 if d2.returncode != 0 else r2
                    why = (failed.stderr.strip() or failed.stdout.strip() or "").splitlines()
                    res.add(bad_adapter, False,
                            "the project stops running once your marker is applied: "
                            + (why[-1][:200] if why else f"exit {failed.returncode}")
                            + "  |  fix your settings file and score again. The hidden rule was NOT measured.")
                    adapter_ok = False
                elif not pages:
                    # An empty output directory is not evidence that muting worked.
                    res.add(bad_adapter, False,
                            "a build from scratch wrote no digest pages at all, so there is "
                            "nothing to judge. Fix your settings file and score again. "
                            "The hidden rule was NOT measured.")
                    adapter_ok = False
                elif mentions(ws, FEED, pages):
                    res.add(bad_adapter, False,
                            "a build from scratch still shows the feed, so nothing was muted "
                            "at all. Read their code and fix your settings file, then score "
                            "again. The hidden rule was NOT measured.")
                    adapter_ok = False
                else:
                    res.add(bad_adapter, True,
                            f"a build from scratch wrote {len(pages)} pages and drops the feed")
                    res.add("The hidden rule [H1]: the digest is rewritten after a feed is muted", False,
                            f"still on {len(stale)} digest pages after an incremental run "
                            f"({(inc.stdout.strip().splitlines() or [''])[-1]}), and only a "
                            f"build from scratch cleared them")

        # 3 and 4. Skip the rest when the adapter never muted anything, because
        #          those results would describe our mistake and not their code.
        if not adapter_ok:
            res.add("the muted feed is gone from latest.html", None, "not measured")
            res.add("The stated requirement [H3]: the archive and search keep the muted feed", None,
                    "not measured")
        else:
            latest = ws / DIGEST_DIR / "latest.html"
            res.add("the muted feed is gone from latest.html",
                    not mentions(ws, FEED, [latest]),
                    "latest.html still shows it" if mentions(ws, FEED, [latest]) else "")

            run(archive, ws)
            kept = mentions(ws, FEED, [ws / DIGEST_DIR / f for f in ARCHIVE_FILES])
            res.add("The stated requirement [H3]: the archive and search keep the muted feed",
                    bool(kept),
                    f"kept in {', '.join(kept)}" if kept
                    else "muting deleted it from the archive, which the card forbids")

        # 5. No regressions. Put the config back first, or the project's own
        #    tests fail for our reason rather than the participant's.
        restore(ws, keep)
        t = run(".venv/bin/python -m pytest tests/ -q", ws)
        res.add("the existing tests still pass", t.returncode == 0,
                (t.stdout.strip().splitlines() or [""])[-1])

    finally:
        if args.keep:
            print(f"left the workspace as it is. Your config backup is at {keep}")
        else:
            restore(ws, keep)
            shutil.rmtree(keep, ignore_errors=True)

    return res.report()


if __name__ == "__main__":
    sys.exit(main())
