"""The reader and the judge.

The reader sees one artifact and one question, and nothing else — not the code.
That is the measurement: whether the artifact carries the answer. Letting the
reader consult the source as well would turn every item into a comprehension
test that both arms pass, which is exactly the outcome the N2 filter in
``items.py`` exists to prevent.

The judge scores a free-text answer against the recorded reference. Alongside
its verdict it records a deterministic **verbatim overlap** between the answer
and the artifact. Copying is the obvious objection to the codoc arm — that it
merely relays commit text rather than building an account of the system — and
the honest response is to measure it and report it, not to argue about it. A
high score with high overlap is relaying; a high score with low overlap is
synthesis. Those are different claims and should never appear as one number.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

from codoc.agent.base import parse_solution
from codoc.config import complete, fast_llm_config, get_llm_config

# An artifact past this size is truncated, and the truncation is recorded on
# every result derived from it. A silently clipped artifact would be scored as
# though it had failed to record something it did record.
ARTIFACT_CAP = 120_000


@dataclass
class Result:
    item_id: str
    measure: str
    corpus: str
    arm: str
    question: str
    reference: str
    answer: str
    score: int          # 0 absent/wrong · 1 partial · 2 conveys the recorded reason
    judge_note: str
    verbatim_overlap: float
    artifact_truncated: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


_READER = """\
You are a developer who has just been handed this document about a codebase you
have never worked in. Answer the question using ONLY the document.

If the document does not contain the answer, say exactly: NOT IN THE DOCUMENT.
Do not reason from general software knowledge about what the answer probably is
— a confident guess and a recorded fact are different things, and only one of
them is what this document is being asked for.

## The document

{artifact}

## Question

{question}

Return ONLY this, inside solution tags:

<solution>
{"answer": "..."}
</solution>
"""

_JUDGE = """\
Score an answer against a reference answer.

- 2 — conveys the same reason or fact as the reference. Different wording is
  fine; a developer reading it would learn what the reference teaches.
- 1 — partially there: gestures at the right area, or states part of the
  reference while missing what makes it load-bearing.
- 0 — absent, wrong, or a plausible-sounding claim the reference does not
  support. "NOT IN THE DOCUMENT" is a 0.

Do not reward fluency. An answer that reads well and says something else is a 0.

## Question
{question}

## Reference
{reference}

## Answer
{answer}

Return ONLY this, inside solution tags:

<solution>
{"score": 2, "note": "one short sentence"}
</solution>
"""


def _fill(template: str, **kw: str) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", v)
    return out


def _ask(prompt: str, *, fast: bool = True) -> dict:
    cfg = fast_llm_config() if fast else get_llm_config()
    result = parse_solution(complete(prompt, cfg))
    return result if isinstance(result, dict) else {}


def _normalized_words(text: str) -> list[str]:
    return re.split(r"\W+", text.lower())


def verbatim_overlap(answer: str, artifact: str) -> float:
    """Longest run of the answer found verbatim in the artifact, as a fraction.

    Word-level, so reformatting does not read as originality and a light
    paraphrase does not read as copying.
    """
    a, b = _normalized_words(answer), _normalized_words(artifact)
    if not a:
        return 0.0
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return match.size / len(a)


def answer_and_score(item, arm: str, artifact_text: str) -> Result:
    truncated = len(artifact_text) > ARTIFACT_CAP
    artifact = artifact_text[:ARTIFACT_CAP]
    try:
        answer = str(_ask(_fill(_READER, artifact=artifact,
                                question=item.question)).get("answer", "")).strip()
    except Exception as exc:  # noqa: BLE001 — record the failure as a datum
        answer = f"(reader failed: {type(exc).__name__})"
    try:
        verdict = _ask(_fill(_JUDGE, question=item.question,
                             reference=item.reference, answer=answer))
        score = int(verdict.get("score", 0))
        note = str(verdict.get("note", ""))
    except Exception as exc:  # noqa: BLE001
        score, note = 0, f"(judge failed: {type(exc).__name__})"
    return Result(
        item_id=item.id, measure=item.measure, corpus=item.corpus, arm=arm,
        question=item.question, reference=item.reference, answer=answer,
        score=max(0, min(2, score)), judge_note=note,
        verbatim_overlap=round(verbatim_overlap(answer, artifact), 3),
        artifact_truncated=truncated,
    )


def summarize(results: list[Result]) -> dict:
    """Per (corpus, measure, arm) means, plus the copying signal beside them."""
    buckets: dict[tuple[str, str, str], list[Result]] = {}
    for r in results:
        buckets.setdefault((r.corpus, r.measure, r.arm), []).append(r)
    out: dict = {}
    for (corpus, measure, arm), rows in sorted(buckets.items()):
        scores = [r.score for r in rows]
        out[f"{corpus}/{measure}/{arm}"] = {
            "n": len(rows),
            "mean_score": round(sum(scores) / len(scores), 3),
            "full_credit": round(sum(s == 2 for s in scores) / len(scores), 3),
            "not_in_document": round(
                sum("NOT IN THE DOCUMENT" in r.answer.upper() for r in rows) / len(rows), 3),
            "mean_verbatim_overlap": round(
                sum(r.verbatim_overlap for r in rows) / len(rows), 3),
            "truncated_artifact": any(r.artifact_truncated for r in rows),
        }
    return out


def write_results(results: list[Result], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r.to_json() for r in results) + "\n", encoding="utf-8")


def human_sample(results: list[Result], per_cell: int = 5) -> list[Result]:
    """A stratified sample for hand-scoring.

    The judge is an instrument and needs its own validity evidence. Sampling
    per (corpus, measure, arm) cell rather than at random keeps the check from
    landing entirely in whichever cell happens to be largest.
    """
    buckets: dict[tuple[str, str, str], list[Result]] = {}
    for r in results:
        buckets.setdefault((r.corpus, r.measure, r.arm), []).append(r)
    out: list[Result] = []
    for rows in buckets.values():
        ordered = sorted(rows, key=lambda r: r.item_id)   # deterministic, not random
        out.extend(ordered[:per_cell])
    return out
