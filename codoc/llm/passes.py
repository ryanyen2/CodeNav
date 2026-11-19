"""The LLM passes: one function per prompt in ``codoc/prompts``.

Each fills its template, runs it, and returns the model's answer as plain
dicts. None of them reads the store or writes anything; the loops decide what
an answer becomes.

Bootstrap (``loops/bootstrap.py``):

* :func:`propose_brief`: one call before anything else, over the project's own
  prose: README, package metadata, module docstrings and leading comments. It is
  the only pass that reads what the project says about itself rather than what
  its code does. Its output rides in every later bootstrap prompt, so a file is
  described as part of something rather than on its own terms.
* :func:`propose_file_features_raw`: one call per source file. The model sees
  one file's chunks, so it cannot put unrelated symbols from other files into
  the same node. It returns ``add_node`` ops, optionally nested.
* :func:`propose_organization_raw`: one call after every file is processed.
  Given the file-level features and their call/import coupling, it groups them
  under a few theme parents (``add_node`` themes, ``move_node`` of features).

A new node carries a temporary id in its ``id`` field; a child or a moved
feature names it as ``parent_id``, and the caller maps those to real ids. The
organizing call also names existing features by alias (``f3``), which the
caller maps back the same way.

Reflect (``loops/reflect.py``):

* :func:`propose_tree_update_raw`: one call over a whole change set, the
  relevant subtree and every node title. Seeing all candidates and all titles
  at once is what lets it fold sibling chunks into one node.

Translate (``loops/translate.py``):

* :func:`propose_translations`: one call per batch of features.
"""
from __future__ import annotations

import json
import logging

from codoc.doclang import DocLanguage
from codoc.llm.prompt import format_prompt, load_prompt, run_agent, split_prompt, titles_outline
from codoc.llm.provider import LLMConfig, fast_llm_config

_log = logging.getLogger(__name__)


def _call(name: str, config: LLMConfig | None, *, doc_language: DocLanguage | None = None, **values) -> dict | list:
    """Fill prompt ``name`` with ``values`` and run it. Everything before the
    template's last cache break is sent as the cached prefix."""
    prefix_tpls, volatile_tpl = split_prompt(load_prompt(name, doc_language=doc_language))
    prefix_parts = [format_prompt(t, **values) for t in prefix_tpls]
    return run_agent(format_prompt(volatile_tpl, **values), config, prefix_parts=prefix_parts)


def raw_ops(raw: dict | list) -> list[dict]:
    """The model's ops as plain dicts in the prompt's JSON shape. An
    ``add_node`` carries its temporary ``id`` (``"n1"``) that later ops in the
    same reply reference as ``parent_id``; the caller remaps."""
    ops_raw = raw.get("ops", []) if isinstance(raw, dict) else raw
    return [o for o in (ops_raw or []) if isinstance(o, dict) and isinstance(o.get("kind"), str)]


# ── bootstrap ────────────────────────────────────────────────────────────────

def propose_brief(
    readme: str,
    headers: list[dict],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
    doc_language: DocLanguage | None = None,
) -> dict:
    """One LLM call: read the project's own prose and form a picture of the whole.

    Runs before any file is described. Everything it produces is handed to every
    per-file call, because without it each file is named on its own
    terms, and a tree assembled from twelve independent readings has no account
    of what the program is for or which of its rules were choices.

    Returns a dict with purpose, audience, out_of_scope, vocabulary, decisions
    and ordering. Every field may be empty. An empty brief is worth more
    than a guessed one, and the file prompt is written to cope with either.
    """
    raw = _call(
        "bootstrap_brief", config or fast_llm_config(), doc_language=doc_language,
        repo_name=repo_name,
        readme=readme.strip() or "(this project has no README)",
        headers=json.dumps(headers, indent=2, ensure_ascii=False),
    )
    if not isinstance(raw, dict):
        return {}
    return raw


def format_brief(brief: dict | None) -> str:
    """The brief as prose for a prompt, or a line saying there is none.

    Rendered rather than passed as JSON because it is read, not parsed: the file
    pass has to weigh it against the code in front of it, and a paragraph is
    easier to weigh than a nested object.
    """
    none = "(no brief: describe this file on its own terms)"
    if not brief:
        return none
    out: list[str] = []
    if brief.get("purpose"):
        out.append(f"For: {brief['purpose']}")
    if brief.get("audience"):
        out.append(f"Output read by: {brief['audience']}")
    if brief.get("out_of_scope"):
        out.append("Not in scope: " + "; ".join(str(x) for x in brief["out_of_scope"]))
    terms = [f"- {v['term']}: {v.get('means', '')}" for v in brief.get("vocabulary") or [] if v.get("term")]
    if terms:
        out.append("Terms with a meaning of their own here:\n" + "\n".join(terms))
    def sentence(s) -> str:
        return str(s).strip().rstrip(".") + "."

    # The cost of a choice and the failure of an order follow as plain clauses,
    # not as labels: a file pass copies a label into a description with the fact.
    def clause(s) -> str:
        s = sentence(s)
        return s[:1].lower() + s[1:] if s[1:2].islower() else s

    choices = []
    for d in brief.get("decisions") or []:
        if d.get("choice"):
            line = f"- {sentence(d['choice'])}"
            if d.get("because"):
                line = f"- {sentence(d['choice'])[:-1]}, because {sentence(d['because'])}"
            if d.get("gave_up"):
                line += f" In exchange, {clause(d['gave_up'])}"
            choices.append(line)
    if choices:
        out.append("Choices where another was possible:\n" + "\n".join(choices))
    order = []
    for o in brief.get("ordering") or []:
        if o.get("before"):
            line = f"- {sentence(str(o['before']) + ' runs before ' + str(o.get('then', '')))}"
            if o.get("otherwise"):
                line += f" If not, {clause(o['otherwise'])}"
            order.append(line)
    if order:
        out.append("Order that matters:\n" + "\n".join(order))
    return "\n\n".join(out) or none


def propose_file_features_raw(file, chunks, edges, existing_titles, *, repo_name="codebase", config=None,
                              why=None, brief=None, doc_language=None) -> list[dict]:
    return raw_ops(_call(
        "bootstrap_file", config or fast_llm_config(), doc_language=doc_language,
        repo_name=repo_name, file=file,
        chunks=json.dumps(chunks, indent=2, sort_keys=True, ensure_ascii=False),
        edges=json.dumps(edges, indent=2, sort_keys=True, ensure_ascii=False),
        existing_titles="\n".join(f"- {t}" for t in existing_titles) or "(none yet)",
        brief=format_brief(brief),
        why=(json.dumps(why, indent=2, sort_keys=True, ensure_ascii=False) if why
             else "(no commit history recorded for this file)"),
    ))


def propose_organization_raw(features, edges, *, repo_name="codebase", config=None, flows=None,
                             brief=None, doc_language=None) -> list[dict]:
    return raw_ops(_call(
        "bootstrap_org", config, doc_language=doc_language,
        repo_name=repo_name,
        features=json.dumps(features, indent=2, ensure_ascii=False),
        edges=json.dumps(edges, indent=2, ensure_ascii=False),
        flows="\n".join(f"- {f}" for f in flows) if flows else "(no call paths could be derived)",
        brief=format_brief(brief),
    ))


# ── reflect ──────────────────────────────────────────────────────────────────

def propose_tree_update_raw(
    changes: dict,
    subtree: list[dict],
    all_titles: list[dict],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
    doc_language: DocLanguage | None = None,
) -> list[dict]:
    """The model's ops as plain dicts in the prompt's JSON shape (``kind``,
    ``feature_id``, ``blocks``…). The reflect pass interprets them. A reply
    that cannot be parsed yields no ops rather than failing the pass."""
    try:
        raw = _call(
            "tree_update", config or fast_llm_config(), doc_language=doc_language,
            repo_name=repo_name,
            changes=json.dumps(changes, indent=2, sort_keys=True, ensure_ascii=False),
            subtree=json.dumps(subtree, indent=2, sort_keys=True, ensure_ascii=False),
            all_titles=titles_outline(all_titles),
        )
    except Exception as exc:  # noqa: BLE001 (a bad reply must not sink the pass)
        _log.warning("codoc: unparseable tree-update response (%s); no ops", exc)
        return []
    return raw_ops(raw)


# ── translate ────────────────────────────────────────────────────────────────

def propose_translations(
    features: list[dict],
    language: DocLanguage,
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> dict[str, tuple[str, dict[str, str]]]:
    """Translate a batch of ``{id, title, blocks: [{id, text}]}`` into ``language``.

    Returns ``{feature_id: (title, {block_id: text})}`` for every item the
    model returned usably, omitting the rest. Omission is not an error: the
    caller reports untranslated nodes and leaves them in their original language.

    Batched because a translator benefits from seeing sibling nodes: consistent
    terminology is most of what makes a translated tree read as one document.
    Which nodes to send and whether a translation is safe to apply are the
    caller's rules (``loops/translate.py``), so they are testable without an LLM.

    Not the fast model tier. Every other pass emits a short op list against
    explicit rules; this one rewrites a person's prose, where the cheap tier's
    fluency ceiling is what the reader would notice.
    """
    if not features:
        return {}
    raw = _call(
        "translate", config,
        repo_name=repo_name,
        language=language.name,
        title_rule=f"A title is {language.title_rule}.",
        prose_rule=language.prose_rule,
        features=json.dumps(features, indent=2, ensure_ascii=False),
    )
    rows = raw.get("features", []) if isinstance(raw, dict) else raw
    out: dict[str, tuple[str, dict[str, str]]] = {}
    for row in rows if isinstance(rows, list) else []:
        fid = str(row.get("id") or "").strip() if isinstance(row, dict) else ""
        title, blocks = (row.get("title"), row.get("blocks") or []) if fid else (None, None)
        if not fid or not isinstance(title, str) or not isinstance(blocks, list):
            _log.warning("codoc translate: dropping malformed row %r", row)
            continue
        out[fid] = (title.strip(), {str(b["id"]): b["text"] for b in blocks
                                    if isinstance(b, dict) and isinstance(b.get("text"), str) and b.get("id")})
    return out
