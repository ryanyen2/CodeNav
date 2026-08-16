#!/usr/bin/env python3
"""Can somebody answer the quiz from the description alone?

    python3 check-description-answers.py <workspace> <project>
    python3 check-description-answers.py ~/codoc-study/scribe scribe
    python3 check-description-answers.py --blind scribe

This is the test the study rests on. The claim is that a written description
helps somebody build a theory of a program they did not write, and the quiz is
how that is measured. If the description does not contain the answers, the quiz
measures whether people can guess, and no amount of running participants fixes
that.

It reads the description — the codoc tree or CLAUDE.md, whichever the workspace
has — and for each question asks a model to answer using ONLY that text, with a
confidence. What comes out is not a claim about what a person would score. It is
a floor: a question the model cannot answer from the description is a question no
participant could have answered from the description either.

`--blind` answers the same quiz with NO description at all. That number is the
one to worry about. A question answerable from the name of the program and
ordinary sense is a question that discriminates nothing, because both conditions
would get it, and a quiz full of those measures how good somebody is at
multiple-choice rather than what the session taught them. The gap between the two
runs is what the quiz is actually worth.

Needs OPENAI_API_KEY. It makes one call per question.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECTS = HERE.parent / "projects"

QUESTION = re.compile(r"^\*\*Q(\d+)\.\s*(.+?)\*\*\s*$")
OPTION = re.compile(r"^-\s*([a-d])\)\s*(.+?)\s*$")
BAND = re.compile(r"^###\s+(.+?)(?:\s+—.*)?$")


def read_quiz(project: str) -> list[dict]:
    text = (PROJECTS / project / "STUDY.md").read_text(encoding="utf-8")
    start = text.index("## The quiz")
    body = text[start:]
    end = body.find("\n## ", 1)
    if end > 0:
        body = body[:end]

    questions: list[dict] = []
    band = ""
    for line in body.split("\n"):
        line = line.strip()
        if m := BAND.match(line):
            band = m.group(1)
        elif m := QUESTION.match(line):
            questions.append({"n": int(m.group(1)), "band": band,
                              "question": m.group(2), "options": [], "answer": None})
        elif (m := OPTION.match(line)) and questions:
            letter, raw = m.group(1), m.group(2)
            correct = raw.rstrip().endswith("✓")
            clean = raw.rstrip().removesuffix("✓").strip().strip("*").strip()
            questions[-1]["options"].append({"letter": letter, "text": clean})
            if correct:
                questions[-1]["answer"] = letter
    return questions


def read_description(workspace: Path) -> tuple[str, str]:
    """The description, and what kind it is."""
    tree = workspace / ".codoc" / "tree.codoc"
    if tree.is_file():
        return tree.read_text(encoding="utf-8"), "codoc feature tree"
    claude = workspace / "CLAUDE.md"
    if claude.is_file():
        return claude.read_text(encoding="utf-8"), "CLAUDE.md"
    raise SystemExit(f"no description in {workspace}: no .codoc/tree.codoc and no CLAUDE.md")


BLIND_PROMPT = """You are answering a multiple-choice question about a program \
called "{project}". You have never seen it and you have no description of it.

Answer from ordinary sense and from what a program with that name plausibly does.
Guess if you must; do not refuse.

QUESTION
--------
{question}

{options}

Reply with JSON only:
{{"answer": "<a|b|c|d>", "grounded": false, "evidence": ""}}"""

PROMPT = """You are answering a multiple-choice question about a program you have \
never seen. You have ONE source: the written description below. You may not \
reason from what such a program usually does, from the names of things, or from \
general programming knowledge — only from what this text actually says.

DESCRIPTION
-----------
{description}

QUESTION
--------
{question}

{options}

Reply with JSON only:
{{"answer": "<a|b|c|d>",
  "grounded": <true if the description states or clearly implies the answer, \
false if you are guessing or reasoning from outside it>,
  "evidence": "<the sentence from the description that settles it, or empty>"}}"""


def ask(client, model, description: str | None, q: dict, project: str = "") -> dict:
    options = "\n".join(f"{o['letter']}) {o['text']}" for o in q["options"])
    body = (BLIND_PROMPT.format(project=project, question=q["question"], options=options)
            if description is None
            else PROMPT.format(description=description, question=q["question"], options=options))
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": body}],
        max_completion_tokens=2000,
    )
    text = response.choices[0].message.content or ""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"answer": None, "grounded": False, "evidence": ""}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"answer": None, "grounded": False, "evidence": ""}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    blind = sys.argv[1] == "--blind"
    project = sys.argv[2]
    if blind:
        description, kind = None, "nothing at all"
    else:
        description, kind = read_description(Path(sys.argv[1]).expanduser())
    questions = read_quiz(project)
    if not questions:
        print(f"no quiz found for {project}")
        return 2

    try:
        import openai
    except ImportError:
        print("needs the openai package")
        return 2
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("needs OPENAI_API_KEY")
        return 2
    client = openai.OpenAI(api_key=key, timeout=180)
    model = os.environ.get("CODOC_MODEL", "gpt-5.6-luna")

    size = "" if description is None else f" ({len(description.splitlines())} lines)"
    print(f"\n{project}, from the {kind}{size}\n")
    right = grounded = 0
    gaps: list[dict] = []

    for q in questions:
        result = ask(client, model, description, q, project)
        ok = result.get("answer") == q["answer"]
        has = bool(result.get("grounded"))
        right += ok
        grounded += has and ok
        mark = "ok  " if ok and has else "weak" if ok else "MISS"
        print(f"  {mark}  Q{q['n']:<2} [{q['band']:<9}] {q['question'][:58]}")
        if not (ok and has):
            gaps.append({**q, "got": result.get("answer"), "grounded": has})

    print(f"\n  {right}/{len(questions)} answered correctly, "
          f"{grounded} of those grounded in the description\n")

    if gaps:
        print("  Not answerable from the description as written:\n")
        for q in gaps:
            wanted = next(o["text"] for o in q["options"] if o["letter"] == q["answer"])
            print(f"    Q{q['n']} [{q['band']}] {q['question']}")
            print(f"      the description would have to say: {wanted}\n")

    if blind:
        print("  This is the floor. Anything answerable here is answerable without")
        print("  reading anything, so it cannot tell the two conditions apart.")
        print(f"  A quiz worth running scores near chance blind, which is "
              f"{len(questions) // 4} of {len(questions)}.")
        return 0

    # A quiz the description cannot answer is a quiz about guessing. Two thirds
    # grounded is the bar for a description that is doing its job.
    floor = int(len(questions) * 0.67)
    if grounded < floor:
        print(f"  BELOW THE BAR: {grounded} grounded, wanted at least {floor}.")
        print("  Either the description is not saying enough, or the quiz is asking")
        print("  about things nobody wrote down. Both are fixable; running")
        print("  participants against this is not.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
