"""Question generation, and the filter that decides whether a question counts.

The design constraint that shapes everything here: **ground truth must not come
from either artifact under test.** Generating questions from the codoc tree and
then asking whether the codoc tree answers them measures nothing. So N2 items
are generated from commit history — a third source both arms could have read and
neither is scored on directly.

The filter is the other half, and it matters more than the generator. A question
like "why does this retry?" is worthless if the answer is legible in the code
itself, because then it measures reading comprehension and every arm that
includes a code pointer wins. So every candidate is put to a reader that sees
**only the current code** and no history. If that reader answers it, the item is
discarded. What survives is the set of questions whose answers exist only in the
project's record — which is precisely the N2 claim.

This costs roughly two extra model calls per surviving item. It is the price of
the measurement being about rationale rather than about legibility, and it is
not optional.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from codoc.agent.base import parse_solution
from codoc.config import complete, fast_llm_config, get_llm_config
from codoc.loop.why import _body_gist, _is_noise

_CODE_CAP = 6000
_DIFF_CAP = 4000


@dataclass
class Item:
    id: str
    measure: str        # "n1" | "n2"
    corpus: str
    question: str
    reference: str      # the recorded answer, in the record's own terms
    source: str         # commit sha (n2) or file path (n1)
    context: str = ""   # what the code-only prober was shown, kept for audit

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:12]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, errors="replace")
    return proc.stdout if proc.returncode == 0 else ""


# ─── N2: rationale, harvested from history ──────────────────────────────────

def reasoned_commits(repo: Path, subdir: str = "", *, limit: int = 60) -> list[dict]:
    """Commits whose message states a reason, touching code that still exists.

    "Still exists" is load-bearing. A reason for code that has since been
    deleted is unanswerable from any artifact describing the current tree, and
    scoring both arms as failures on it would just add noise to both.
    """
    scope = ["--", subdir] if subdir else []
    raw = _git(repo, "log", "--no-merges", "-n", "400",
               "--format=%x1e%H%x1f%s%x1f%b%x1f", "--name-only", *scope)
    out: list[dict] = []
    for rec in raw.split("\x1e"):
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        sha, subject, body = parts[0].strip(), parts[1].strip(), parts[2]
        if not subject or _is_noise(subject):
            continue
        gist = _body_gist(body)
        if len(gist) < 80:          # a subject alone rarely states a reason
            continue
        alive = [f for f in (ln.strip() for ln in parts[3].splitlines())
                 if f and (not subdir or f.startswith(subdir)) and (repo / f).exists()]
        if not alive:
            continue
        out.append({"sha": sha, "subject": subject, "why": gist, "files": alive[:3]})
        if len(out) >= limit:
            break
    return out


_N2_GEN = """\
You are building a comprehension test for developers who must modify a codebase
they did not write.

Below is a commit from the project's history — its message states why a change
was made — together with the CURRENT contents of a file it touched.

Write ONE question that a developer about to modify this code would need
answered, whose answer is the REASON recorded in the commit message. The
question must:

- name the code plainly (file and symbol) so it is answerable without seeing
  this commit;
- ask WHY the code is the way it is, or what constraint it must respect — not
  what it does;
- be answerable in two or three sentences;
- NOT quote or paraphrase the commit message. A reader must not be able to
  recover the answer from the wording of the question.

Then write the reference answer, in your own words, from the commit message
only. If the message states no real reason — if it just describes the edit —
return `{"skip": "no reason stated"}` instead.

## Commit

{commit}

## Current contents of {file}

{code}

Return ONLY this, inside solution tags:

<solution>
{"question": "...", "reference": "..."}
</solution>
"""

_CODE_ONLY_PROBE = """\
Answer the question using ONLY the code below. If the code does not support an
answer, say exactly: INSUFFICIENT.

Do not speculate, and do not offer a plausible-sounding reason you cannot point
to in the code. An honest INSUFFICIENT is the correct answer more often than not.

## Question

{question}

## Code — {file}

{code}

Return ONLY this, inside solution tags:

<solution>
{"answer": "..."}
</solution>
"""

_LEAK_CHECK = """\
Two texts: a reference answer, and an answer written from the code alone by
someone who never saw the project's history.

Does the code-only answer convey the same reason as the reference? Answer yes
only if a developer reading it would learn the same constraint or motivation —
not merely a related fact about the same code.

## Reference
{reference}

## Code-only answer
{candidate}

Return ONLY this, inside solution tags:

<solution>
{"same_reason": true}
</solution>
"""


def _ask(prompt: str, *, fast: bool = True) -> dict:
    cfg = fast_llm_config() if fast else get_llm_config()
    raw = complete(prompt, cfg)
    result = parse_solution(raw)
    return result if isinstance(result, dict) else {}


def _fill(template: str, **kw: str) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", v)
    return out


def make_n2_items(repo: Path, corpus: str, commits: list[dict]) -> list[Item]:
    """Generate rationale items, keeping only those the code cannot answer."""
    items: list[Item] = []
    for c in commits:
        file = c["files"][0]
        code = (repo / file).read_text(encoding="utf-8", errors="replace")[:_CODE_CAP]
        diff = _git(repo, "show", "--stat", "--format=", c["sha"])[:_DIFF_CAP]
        commit_text = f"{c['subject']}\n\n{c['why']}\n\nFiles touched:\n{diff}"
        try:
            gen = _ask(_fill(_N2_GEN, commit=commit_text, file=file, code=code))
        except Exception:  # noqa: BLE001 — one bad generation must not stop the run
            continue
        if gen.get("skip") or not gen.get("question") or not gen.get("reference"):
            continue
        if _answerable_from_code(gen["question"], file, code, gen["reference"]):
            continue  # measures legibility, not rationale
        items.append(Item(
            id=_sha(corpus, c["sha"], gen["question"]),
            measure="n2", corpus=corpus, question=gen["question"],
            reference=gen["reference"], source=c["sha"], context=file,
        ))
    return items


def _answerable_from_code(question: str, file: str, code: str, reference: str) -> bool:
    """True when a reader with only the code recovers the recorded reason."""
    try:
        probe = _ask(_fill(_CODE_ONLY_PROBE, question=question, file=file, code=code))
    except Exception:  # noqa: BLE001
        return False   # a failed probe must not silently drop a good item
    answer = str(probe.get("answer", "")).strip()
    if not answer or answer.upper().startswith("INSUFFICIENT"):
        return False
    try:
        verdict = _ask(_fill(_LEAK_CHECK, reference=reference, candidate=answer))
    except Exception:  # noqa: BLE001
        return False
    return bool(verdict.get("same_reason"))


# ─── N1: functionality and structural recall ────────────────────────────────

_N1_GEN = """\
Write {n} questions testing whether a developer, given only a description of
this codebase, could find their way around it.

Mix three kinds:
- what a named part of the system is for;
- where a given kind of change would have to be made;
- how two parts relate (what calls or depends on what).

Answer each from the code below. Questions must be answerable from a good
overview document — do not ask about a line-level detail no summary would carry.

## Files

{files}

Return ONLY this, inside solution tags:

<solution>
{"items": [{"question": "...", "reference": "..."}]}
</solution>
"""


def make_n1_items(repo: Path, corpus: str, files: list[str], *, n: int = 10) -> list[Item]:
    blob = []
    for f in files[:12]:
        text = (repo / f).read_text(encoding="utf-8", errors="replace")[:2500]
        blob.append(f"### {f}\n{text}")
    try:
        gen = _ask(_fill(_N1_GEN, n=str(n), files="\n\n".join(blob)))
    except Exception:  # noqa: BLE001
        return []
    out: list[Item] = []
    for raw in gen.get("items", []):
        q, ref = raw.get("question"), raw.get("reference")
        if q and ref:
            out.append(Item(id=_sha(corpus, q), measure="n1", corpus=corpus,
                            question=q, reference=ref, source=",".join(files[:3])))
    return out


def source_files(repo: Path, subdir: str = "") -> list[str]:
    root = repo / subdir if subdir else repo
    return sorted(
        str(p.relative_to(repo))
        for p in root.rglob("*.py")
        if "test" not in p.parts and not p.name.startswith("_test")
    )


def write_items(items: list[Item], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(i.to_json() for i in items) + "\n", encoding="utf-8")


def read_items(path: Path) -> list[Item]:
    return [Item(**json.loads(ln))
            for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
