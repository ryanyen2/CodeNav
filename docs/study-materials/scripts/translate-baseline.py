#!/usr/bin/env python3
"""Translate the baseline arm's description, the way codoc translates the tree.

    python3 translate-baseline.py <workspace> <bcp47>
    python3 translate-baseline.py ~/work/scribe-baseline zh-Hans

The codoc arm's feature tree is translated by `codoc translate`, which is part of
the tool being studied. The baseline arm's CLAUDE.md has no such command, and
leaving it in English while the tree is translated would make LANGUAGE vary with
CONDITION — every result would then be as attributable to reading in a second
language as to the tool. This is the other half, held to the same contract:

    Prose is translated. Addresses are not.

Everything a reader could type, run, or search for stays verbatim: fenced code
blocks, inline `code`, file paths, identifiers, constants, and the two project
names. That is not politeness about jargon — the quiz asks questions like "where
does a new policy go?", whose answer is the string `convert.py`, and a translated
file name sends somebody looking for a file that does not exist.

It refuses rather than writes when a code span or a fenced block goes missing,
because a description that lost its identifiers is worse than one nobody
translated: it looks finished.

Needs OPENAI_API_KEY. One call per file.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`\n]+`")

LANGUAGES = {
    "zh-Hans": "Simplified Chinese (简体中文)",
    "zh-Hant": "Traditional Chinese (繁體中文)",
    "ja": "Japanese (日本語)",
}

PROMPT = """You are translating a software project's README-style description into {name}.

Translate the PROSE only. Everything below must survive EXACTLY as it appears,
character for character, and must not be translated, transliterated, reordered or
reformatted:

  - every fenced code block (``` … ```) including its contents
  - every inline code span (`like_this`)
  - file names and paths: lines.py, convert.py, fixtures/, tests/
  - identifiers and constants: COLUMNS, RULES, KEEP_HYPHEN, MAX_HEADING_WORDS
  - the project's own name
  - markdown structure: heading levels, list markers, table pipes, blank lines

Keep established English technical terms in English where a working programmer in
that language would use them (CSV, Markdown, PDF, diff, commit, test). A reader is
going to work in this codebase in English; the prose is what changes, not what
they type.

Do not add, drop, summarise or reorder anything. Same number of sections, same
number of bullets, same tables. Return ONLY the translated document.

The document:

{body}"""


def spans(text: str) -> tuple[list[str], list[str]]:
    """The code spans and fenced blocks that must come through untouched."""
    return INLINE.findall(text), FENCE.findall(text)


def translate(body: str, target: str, model: str) -> str:
    from openai import OpenAI

    name = LANGUAGES.get(target, target)
    client = OpenAI()
    reply = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT.format(name=name, body=body)}],
    )
    out = reply.choices[0].message.content or ""
    # A model that decides to be helpful and wrap the answer in a fence would
    # otherwise add a code block that was never in the source.
    out = out.strip()
    if out.startswith("```markdown"):
        out = out.split("\n", 1)[1].rsplit("```", 1)[0]
    elif out.startswith("```") and out.endswith("```"):
        out = out.split("\n", 1)[1].rsplit("```", 1)[0]
    return out.strip() + "\n"


def check(before: str, after: str) -> list[str]:
    """What the translation lost. Empty means it is safe to write."""
    problems = []
    was_inline, was_fenced = spans(before)
    now_inline, now_fenced = spans(after)

    missing = [s for s in set(was_inline) if s not in after]
    if missing:
        problems.append(f"{len(missing)} code span(s) gone, e.g. {missing[0]}")
    if len(now_fenced) != len(was_fenced):
        problems.append(f"{len(was_fenced)} fenced block(s) became {len(now_fenced)}")
    for block in was_fenced:
        if block not in after:
            first = block.splitlines()[1] if len(block.splitlines()) > 1 else block
            problems.append(f"a fenced block was altered, near: {first[:60]}")
            break
    # Headings carry the shape of the document, and a lost one is a lost section.
    was_h = len([l for l in before.splitlines() if l.startswith("#")])
    now_h = len([l for l in after.splitlines() if l.startswith("#")])
    if was_h != now_h:
        problems.append(f"{was_h} heading(s) became {now_h}")
    return problems


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    root, target = Path(sys.argv[1]), sys.argv[2]
    model = os.environ.get("CODOC_MODEL", "gpt-5.6-luna")

    files = [root / "CLAUDE.md"]
    skill = root / ".claude" / "skills" / "doc-maintenance" / "SKILL.md"
    # The instructions the baseline agent works from. Left in English it would
    # write English back into a description the participant is reading in another
    # language, which is the same confound one layer down.
    if skill.exists():
        files.append(skill)

    failed = False
    for path in files:
        if not path.exists():
            print(f"  no {path.name} in {root}")
            failed = True
            continue
        before = path.read_text()
        after = translate(before, target, model)
        problems = check(before, after)
        if problems:
            print(f"  REFUSED {path.relative_to(root)}:")
            for p in problems:
                print(f"    {p}")
            failed = True
            continue
        path.write_text(after)
        print(f"  translated {path.relative_to(root)} ({len(after.splitlines())} lines)")

    if failed:
        print("\nNothing safe to write was left unwritten, but something was refused.")
        print("A description that lost its identifiers looks finished and is not.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
