"""codoc.agent.base — shared utilities for LLM agents.

Moved verbatim from the old ``codoc.agents.base`` (the only agent helper the
rewrite keeps). ``load_prompt`` resolves ``codoc/prompts/{name}.txt`` and expands
``{{include:X}}`` directives; ``format_prompt`` does brace-safe substitution;
``parse_solution`` extracts JSON from ``<solution>`` tags / fences / bare JSON;
``run_agent`` calls the configured LLM and returns the parsed solution.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from pathlib import Path

from codoc.config import LLMConfig, complete, get_llm_config
from codoc.doclang import DocLanguage, prompt_directive, resolve


CACHE_BREAK = "<<<CACHE_BREAK>>>"


def load_prompt(name: str, *, doc_language: DocLanguage | None = None,
                for_code_agent: bool = False) -> str:
    """The prompt template with ``{{include:X}}`` and ``{{doclang}}`` expanded.

    ``{{doclang}}`` expands to the authoring-language directive for
    ``doc_language`` (empty for English, so an English repo's prompt text is
    unchanged). It is expanded HERE, at load time, for the same reason includes
    are: expansion must happen before :func:`split_prompt`, so the directive
    lands inside the cached prefix and cannot be displaced by a marker-shaped
    value substituted later. Omitting the argument yields English — a call site
    that forgets gets the old behavior, never a broken prompt.
    """
    prompts_dir = Path(__file__).parent.parent / "prompts"
    text = (prompts_dir / f"{name}.txt").read_text(encoding="utf-8")

    def _expand(m: re.Match) -> str:
        inc_name = m.group(1).strip()
        inc_path = prompts_dir / f"{inc_name}.txt"
        try:
            return inc_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"[missing include: {inc_name}]"

    text = re.sub(r"\{\{include:(.*?)\}\}", _expand, text)
    return expand_doclang(text, doc_language, for_code_agent=for_code_agent)


def expand_doclang(text: str, doc_language: DocLanguage | None, *,
                   for_code_agent: bool = False) -> str:
    """Replace the ``{{doclang}}`` marker with its directive block.

    For English the directive is empty, and the marker collapses together with the
    blank lines around it into a single paragraph break — so an English prompt
    reads exactly as it did before this feature existed, with no gap where a
    section would have been.
    """
    lang = doc_language or resolve(None)
    block = prompt_directive(lang, for_code_agent=for_code_agent)
    if block:
        return text.replace("{{doclang}}", block)
    # Two cases, because "one paragraph break" is not what a marker at the very end
    # of a template should leave behind: interior markers collapse to a blank line,
    # a trailing one to the file's own single newline.
    text = re.sub(r"\n*[ \t]*\{\{doclang\}\}[ \t]*\n*\Z", "\n", text)
    return re.sub(r"\n*[ \t]*\{\{doclang\}\}[ \t]*\n*", "\n\n", text)


def split_prompt(template: str) -> tuple[list[str], str]:
    """Split a TEMPLATE on ``CACHE_BREAK`` markers.

    Returns ``(prefix_parts, volatile_tail)``: everything before the last
    marker is the stable prefix (one part per marker-delimited segment,
    most-stable-first — the template author orders them), the remainder is the
    per-call tail. A template without markers is all tail (no caching).

    MUST be called on the raw template, BEFORE ``format_prompt`` substitutes
    values: substituted content is repo-derived (chunk source, descriptions),
    and a literal marker inside it — codoc's own source contains one — would
    move the split point and promote untrusted text into the system-prompt
    prefix. Splitting first makes marker-shaped *values* inert.
    """
    if CACHE_BREAK not in template:
        return [], template
    segments = [s.strip("\n") for s in template.split(CACHE_BREAK)]
    return [s for s in segments[:-1] if s.strip()], segments[-1].strip("\n")


def titles_outline(all_titles: list[dict]) -> str:
    """Serialize the every-node-title context as a compact, deterministic
    indented outline (roughly a third the tokens of indented JSON, and byte-
    stable between tree mutations — which is what makes it cacheable)."""
    children: dict[str | None, list[dict]] = {}
    for t in all_titles:
        children.setdefault(t.get("parent_id"), []).append(t)
    for sibs in children.values():
        sibs.sort(key=lambda t: str(t.get("id")))
    lines: list[str] = []

    def _line(t: dict, depth: int) -> str:
        # The planned marker is the outline's one annotation: an accepted plan
        # placeholder is invisible to file-seeded context (it has no bindings),
        # so this line is the only place a reader — the tree-update LLM or an
        # agent reading codoc_context — learns the node exists and is waiting
        # for exactly this code rather than needing a fresh node.
        mark = "  (planned — attach its code, don't duplicate)" if t.get("planned") else ""
        return f"{'  ' * depth}- [{t.get('id')}] {t.get('title', '')}{mark}"

    def _emit(parent: str | None, depth: int, seen: set[str]) -> None:
        for t in children.get(parent, []):
            tid = str(t.get("id"))
            if tid in seen:  # defensive: a parent-cycle must not hang the render
                continue
            seen.add(tid)
            lines.append(_line(t, depth))
            _emit(tid, depth + 1, seen)

    emitted: set[str] = set()
    _emit(None, 0, emitted)
    # EVERY title must appear — this list is the LLM's duplicate-avoidance
    # context. Nodes the root walk can't reach (orphaned parent links, cycle
    # members, and their descendants) are appended flat; omitting them invites
    # the model to mint duplicates of exactly the features that are already in
    # a broken state.
    for t in all_titles:
        if str(t.get("id")) not in emitted:
            lines.append(_line(t, 0))
    return "\n".join(lines) if lines else "(the tree is empty)"


def format_prompt(template: str, **kwargs) -> str:
    """Substitute {variable} placeholders, leaving literal JSON braces intact."""
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def repair_json(text: str) -> str:
    """Escape the two things a model reliably gets wrong inside a JSON string.

    Descriptions are prose, and prose contains quotation marks and line breaks.
    A model asked to state a reason will happily write ``"description": "Signs
    the request the server calls "stale"."`` — the inner quote closes the string
    and the parser then asks for a comma it will never find. Raw newlines inside
    a string fail the same way.

    Both are recoverable without guessing at meaning: walk the text tracking
    string state, and treat a quote as *closing* only when the next non-space
    character is one that can legally follow a string (``,:}]`` or the end).
    Anything else is a quote the model meant literally, so escape it.

    This is a rescue path, not a licence to emit sloppy JSON — the prompts still
    ask for clean output. But one malformed sample used to cost an entire
    bootstrap, and a description is exactly the field most likely to carry a
    quotation.
    """
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        if ch == "\\":            # already escaped — copy the pair verbatim
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == '"':
            nxt = next((c for c in text[i + 1:] if not c.isspace()), "")
            if nxt in (",", ":", "}", "]", ""):
                in_string = False
                out.append(ch)
            else:
                out.append('\\"')  # a quote the model meant as punctuation
            i += 1
            continue
        if ch == "\n":
            out.append("\\n")
            i += 1
            continue
        if ch == "\t":
            out.append("\\t")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads(raw: str) -> dict | list:
    """Strict first, repaired second — so a clean sample is never rewritten."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(repair_json(raw))


def parse_solution(response: str) -> dict | list:
    match = re.search(r"<solution>(.*?)</solution>", response, re.DOTALL)
    if match:
        return _loads(match.group(1).strip())

    fence = re.search(r"```(?:json)?\s*([\[{].*?)\s*```", response, re.DOTALL)
    if fence:
        return _loads(fence.group(1))

    for start_char, end_char in (("[", "]"), ("{", "}")):
        idx = response.find(start_char)
        if idx != -1:
            depth = 0
            in_string = False
            escape = False
            for i, ch in enumerate(response[idx:], start=idx):
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_string:
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return _loads(response[idx : i + 1])
                        except json.JSONDecodeError:
                            break

    raise ValueError(f"No parseable JSON found in LLM response: {response[:300]!r}")


# In-process response memo (bounded LRU). The loops re-derive their state per
# pass, so a pass that failed AFTER its LLM call (apply error, crash-replayed
# batch) re-issues a byte-identical prompt — previously re-billed in full.
# Keyed on provider+model+temperature+the whole prompt; only responses that
# PARSED are cached, so a malformed sample is retried fresh, not replayed.
# Process-local by design: the daemon (where the re-issue storm happens) is
# long-lived; a CLI one-shot simply misses.
_MEMO_MAX = 32
_memo: "OrderedDict[str, str]" = OrderedDict()
_memo_lock = threading.Lock()


def run_agent(
    prompt: str,
    config: LLMConfig | None = None,
    *,
    prefix_parts: list[str] | None = None,
) -> dict | list:
    cfg = config or get_llm_config()
    key = hashlib.sha256(
        "\x1f".join(
            [cfg.provider, cfg.model, str(cfg.temperature), *(prefix_parts or []), prompt]
        ).encode()
    ).hexdigest()
    with _memo_lock:
        cached = _memo.get(key)
        if cached is not None:
            _memo.move_to_end(key)
    if cached is not None:
        return parse_solution(cached)
    response = complete(prompt, cfg, prefix_parts=prefix_parts)
    result = parse_solution(response)  # raises before caching on a bad sample
    with _memo_lock:
        _memo[key] = response
        _memo.move_to_end(key)
        while len(_memo) > _MEMO_MAX:
            _memo.popitem(last=False)
    return result
