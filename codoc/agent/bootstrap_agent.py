"""Bootstrap LLM passes — orientation, per-file feature proposal, organization.

Three scoped calls replace the old flat, attach-biased, global batching that
produced cross-file junk-drawer nodes:

* :func:`propose_brief` — one call before anything else, over the project's own
  prose: README, package metadata, module docstrings and leading comments. It is
  the only pass that reads what the project says about ITSELF rather than what
  its code does, and it exists because the two are different. A file pass can see
  that a hyphen is dropped; only the prose says the typesetter put it there.
  Its output rides in every later prompt, so a file is described as part of
  something rather than on its own terms.

* :func:`propose_file_features` — one call per source file. The model only ever
  sees one file's chunks, so it cannot dump unrelated symbols from other files
  into the same node. It returns a small, coherent set of ``add_node`` ops,
  optionally nested via temporary local ids.
* :func:`propose_organization` — one call after every file is processed. Given
  the file-level features + their call/import coupling, it groups them under a
  few broad theme parents (``add_node`` themes + ``move_node`` of existing
  features), giving the tree real depth.

New nodes carry a temporary local id in the ``id`` field; a child or a moved
feature references it via ``parent_id``. :mod:`codoc.loop.bootstrap_hier`
resolves those local ids to freshly-minted feature ids before applying, which is
what lets a single call nest a new node under another new node — impossible with
the old apply path that minted ids only at write time.
"""
from __future__ import annotations

import json

from codoc.agent.base import format_prompt, load_prompt, run_agent, split_prompt
from codoc.config import LLMConfig, fast_llm_config
from codoc.doclang import DocLanguage


def raw_ops(raw: dict | list) -> list[dict]:
    """The model's ops as plain dicts in the prompt's JSON shape (v2 entry).
    An ``add_node`` carries its temporary ``id`` (``"n1"``) that later ops in the
    same reply reference as ``parent_id``; the caller remaps."""
    ops_raw = raw.get("ops", []) if isinstance(raw, dict) else raw
    return [o for o in (ops_raw or []) if isinstance(o, dict) and isinstance(o.get("kind"), str)]


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
    per-file call, which is the point: without it each file is named on its own
    terms, and a tree assembled from twelve independent readings has no account
    of what the program is for or which of its rules were choices.

    Returns a dict with purpose, audience, out_of_scope, vocabulary, decisions
    and ordering. Every field may be empty — an honest empty brief is worth more
    than a guessed one, and the file prompt is written to cope with either.
    """
    prefix_tpls, volatile_tpl = split_prompt(
        load_prompt("bootstrap_brief", doc_language=doc_language))
    kwargs = dict(
        repo_name=repo_name,
        readme=readme.strip() or "(this project has no README)",
        headers=json.dumps(headers, indent=2, ensure_ascii=False),
    )
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)
    raw = run_agent(volatile, config or fast_llm_config(), prefix_parts=prefix_parts)
    if not isinstance(raw, dict):
        return {}
    return raw


def format_brief(brief: dict | None) -> str:
    """The brief as prose for a prompt, or a line saying there is none.

    Rendered rather than passed as JSON because it is read, not parsed: the file
    pass has to weigh it against the code in front of it, and a paragraph is
    easier to weigh than a nested object.
    """
    if not brief:
        return "(no brief — describe this file on its own terms)"
    out: list[str] = []
    if brief.get("purpose"):
        out.append(f"**What this program is for.** {brief['purpose']}")
    if brief.get("audience"):
        out.append(f"**Who its output is for.** {brief['audience']}")
    if brief.get("out_of_scope"):
        items = "; ".join(str(x) for x in brief["out_of_scope"])
        out.append(f"**Deliberately not in scope.** {items}")
    if brief.get("vocabulary"):
        lines = "\n".join(f"- **{v.get('term','')}** — {v.get('means','')}"
                           for v in brief["vocabulary"] if v.get("term"))
        if lines:
            out.append(f"**Words this codebase uses in its own way**\n{lines}")
    if brief.get("decisions"):
        lines = []
        for d in brief["decisions"]:
            if not d.get("choice"):
                continue
            line = f"- {d['choice']}"
            if d.get("because"):
                line += f" — because {d['because']}"
            if d.get("gave_up"):
                line += f" (what that gives up: {d['gave_up']})"
            lines.append(line)
        if lines:
            out.append("**Choices this project made where another could have chosen "
                       "otherwise**\n" + "\n".join(lines))
    if brief.get("ordering"):
        lines = []
        for o in brief["ordering"]:
            if not o.get("before"):
                continue
            line = f"- {o['before']} runs before {o.get('then','')}"
            if o.get("otherwise"):
                line += f", or {o['otherwise']}"
            lines.append(line)
        if lines:
            out.append("**Order that matters**\n" + "\n".join(lines))
    return "\n\n".join(out) or "(no brief — describe this file on its own terms)"



def propose_file_features_raw(file, chunks, edges, existing_titles, *, repo_name="codebase", config=None,
                              why=None, brief=None, doc_language=None) -> list[dict]:
    prefix_tpls, volatile_tpl = split_prompt(load_prompt("bootstrap_file", doc_language=doc_language))
    kwargs = dict(
        repo_name=repo_name, file=file,
        chunks=json.dumps(chunks, indent=2, sort_keys=True, ensure_ascii=False),
        edges=json.dumps(edges, indent=2, sort_keys=True, ensure_ascii=False),
        existing_titles="\n".join(f"- {t}" for t in existing_titles) or "(none yet)",
        brief=format_brief(brief),
        why=(json.dumps(why, indent=2, sort_keys=True, ensure_ascii=False) if why
             else "(no commit history recorded for this file)"),
    )
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)
    return raw_ops(run_agent(volatile, config or fast_llm_config(), prefix_parts=prefix_parts))



def propose_organization_raw(features, edges, *, repo_name="codebase", config=None, flows=None,
                             brief=None, doc_language=None) -> list[dict]:
    template = load_prompt("bootstrap_org", doc_language=doc_language)
    prompt = format_prompt(
        template, repo_name=repo_name,
        features=json.dumps(features, indent=2, ensure_ascii=False),
        edges=json.dumps(edges, indent=2, ensure_ascii=False),
        flows="\n".join(f"- {f}" for f in flows) if flows else "(no call paths could be derived)",
        brief=format_brief(brief),
    )
    return raw_ops(run_agent(prompt, config))
