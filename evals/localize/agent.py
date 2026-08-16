"""A small, instrumented search agent, and the three artifacts it may be given.

Deliberately not Claude Code or any other full harness. The measurement is how
many looks it takes to find the right code, so the tool surface has to be small
enough that a "look" means one thing, and the loop has to be ours so a step is
counted rather than inferred from a transcript. Four tools, one turn each,
temperature 0.

The three arms differ ONLY in the block of context prepended to the request:

* ``none`` — nothing. The floor.
* ``frozen`` — the document as it was exported at the starting commit, never
  updated since. Same prose, same author, addresses as stale as the repository
  has moved.
* ``live`` — the same document after the loop has maintained it across the same
  history.

Holding the prose constant and varying only whether its addresses were kept
current is the whole point. A "with and without a document" comparison would
measure having a document, which a published study already found does not
generally help coding agents and costs over 20% more tokens (arXiv 2602.11988).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from codoc.config import complete, get_llm_config

MAX_STEPS = 12
_READ_CAP = 4000
_GREP_CAP = 40


SYSTEM = """You are locating code in a repository. Find the files that would have to change to satisfy the request.

Work in steps. Each step, reply with exactly one JSON object and nothing else:

  {"tool": "grep", "pattern": "<regex>"}          search file contents
  {"tool": "list", "path": "<dir>"}               list a directory
  {"tool": "read", "path": "<file>"}              read a file
  {"tool": "done", "files": ["<path>", ...]}      final answer, repo-relative paths

The answer consists of Python source or test files (.py) that already exist in the repository. Do not answer with documentation, changelogs, or configuration.

Look before you answer: make at least two observations, and follow up on what they show, before calling "done". Prefer few, precise files over many."""


@dataclass
class Trace:
    steps: int = 0
    predicted: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


def _grep(repo: Path, pattern: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "grep", "-n", "-I", "-E", pattern],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"(grep failed: {exc})"
    lines = [l for l in out.splitlines() if l.strip()][:_GREP_CAP]
    return "\n".join(lines) if lines else "(no matches)"


def _list(repo: Path, path: str) -> str:
    target = (repo / path.lstrip("/")).resolve()
    # The agent supplies this path; a traversal would read outside the checkout.
    if not str(target).startswith(str(repo.resolve())) or not target.is_dir():
        return "(not a directory)"
    names = sorted(p.name + ("/" if p.is_dir() else "")
                   for p in target.iterdir() if not p.name.startswith("."))
    return "\n".join(names[:120]) or "(empty)"


def _read(repo: Path, path: str) -> str:
    target = (repo / path.lstrip("/")).resolve()
    if not str(target).startswith(str(repo.resolve())) or not target.is_file():
        return "(not a file)"
    try:
        return target.read_text(errors="replace")[:_READ_CAP]
    except OSError as exc:
        return f"(read failed: {exc})"


def _parse(reply: str) -> dict | None:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def locate(repo: Path, request: str, aid: str = "") -> Trace:
    """Run the search loop for one request and return what it did."""
    from codoc.config import usage_snapshot

    trace = Trace()
    before = usage_snapshot()
    context = f"\n\n## The repository's feature document\n\n{aid}\n" if aid else ""
    transcript = [f"{context}\n## Request\n\n{request}"]
    config = get_llm_config()

    def _ask() -> str:
        """One model turn, retried through a transient network failure.

        A burst of connection errors cost 46 of 123 runs on the first altair
        sweep. They fell evenly across the three arms, so the comparison was not
        biased — but a third of the sample went with them, and at 25 runs per arm
        that is the difference between a result and a hint. Retried with a short
        backoff; a persistent failure still raises and is recorded as an error.
        """
        import time
        last: Exception | None = None
        for attempt in range(4):
            try:
                return complete("\n\n".join(transcript), config, prefix_parts=[SYSTEM])
            except Exception as exc:  # noqa: BLE001 — retry anything transient-looking
                last = exc
                if "connection" not in str(exc).lower() and "timeout" not in str(exc).lower():
                    raise
                time.sleep(2 * (attempt + 1))
        raise last  # type: ignore[misc]

    try:
        for _ in range(MAX_STEPS):
            reply = _ask()
            call = _parse(reply)
            trace.steps += 1
            if call is None:
                transcript.append("Reply with one JSON object and nothing else.")
                trace.tool_calls.append("malformed")
                continue
            tool = str(call.get("tool", ""))
            trace.tool_calls.append(tool)
            if tool == "done":
                files = call.get("files") or []
                trace.predicted = [str(f).lstrip("./") for f in files if f][:12]
                break
            if tool == "grep":
                obs = _grep(repo, str(call.get("pattern", "")))
            elif tool == "list":
                obs = _list(repo, str(call.get("path", ".")))
            elif tool == "read":
                obs = _read(repo, str(call.get("path", "")))
            else:
                obs = "(unknown tool)"
            transcript.append(json.dumps(call))
            transcript.append(f"Result:\n{obs}")
    except Exception as exc:  # noqa: BLE001 — one failed task must not end the sweep
        trace.error = f"{type(exc).__name__}: {exc}"[:300]

    spent = usage_snapshot() - before
    trace.input_tokens = spent.input_tokens
    trace.output_tokens = spent.output_tokens
    return trace


def score(predicted: list[str], gold: list[str]) -> dict:
    """File-level precision, recall and F1 against the merged change.

    Reported at file level because that is what the change itself proves and what
    the surrounding literature reports, so the numbers are comparable.
    """
    p, g = set(predicted), set(gold)
    hit = len(p & g)
    precision = hit / len(p) if p else 0.0
    recall = hit / len(g) if g else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "found_any": hit > 0,
        "exact": p == g,
    }
