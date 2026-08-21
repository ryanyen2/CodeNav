"""Bootstrap LLM passes — orientation, per-file feature proposal, organization.

Four scoped calls replace the old flat, attach-biased, global batching that
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
* :func:`propose_settings_features` — one call per settings file the repo's own
  code reads, after every source file. It is the only pass that returns
  ``attach`` and ``amend`` rather than new nodes, because a configured decision
  belongs to the feature whose code reads it and what the tree is missing is the
  VALUE in a description that already exists.
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
from codoc.lang import detect_language
from codoc.loop import prose
from codoc.model.event import NodeOp, NodeOpKind


def _coerce_op(raw: dict) -> NodeOp:
    """Coerce a raw op dict to a NodeOp, carrying a temporary local id.

    For ``add_node`` the model assigns a temporary id (``"id": "n1"``) so other
    ops in the same call can reference it as a ``parent_id``. We stash that
    temporary id in ``feature_id`` (which is otherwise None for a new node); the
    apply step in bootstrap_hier mints the real id and remaps references.
    """
    kind = NodeOpKind(raw["kind"])
    fid = raw.get("feature_id")
    if kind is NodeOpKind.ADD_NODE and not fid:
        fid = raw.get("id")  # temporary local id (e.g. "n1", "t1")
    bindings = [tuple(b) for b in raw.get("bindings", []) if len(b) == 2]
    return NodeOp(
        kind=kind,
        feature_id=fid,
        parent_id=raw.get("parent_id"),
        title=raw.get("title"),
        description=raw.get("description"),
        bindings=bindings,
        rationale=raw.get("rationale", ""),
    )


def _ops_from(raw: dict | list) -> list[NodeOp]:
    ops_raw = raw.get("ops", []) if isinstance(raw, dict) else raw
    return [_coerce_op(o) for o in ops_raw]


def _gated(ops: list[NodeOp], rerun, *, doc_language: DocLanguage | None) -> list[NodeOp]:
    """``ops`` past the prose gate, with one repair attempt (:mod:`codoc.loop.prose`).

    Depth is not passed and that is not an oversight: a bootstrap call proposes a
    file's nodes before the organization pass exists to put them under anything, so
    the only honest answer to "how deep is this" is that nobody knows yet. What IS
    known is which of these nodes has another one under it, and that is the signal
    the altitude rule actually reads: a node with children is a node a reader meets
    on the way down.
    """
    if not ops:
        return ops
    parented = {op.parent_id for op in ops if op.parent_id}
    kept, _findings = prose.gate(
        ops, rerun=rerun, doc_language=doc_language,
        children_of=lambda op: bool(op.local_id and op.local_id in parented),
    )
    return kept


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


def _notebook_note(file: str) -> str:
    """The added instruction block for a notebook, or nothing at all.

    Empty for every other file, so a repository with no notebooks sends the prompt it
    always sent — this cannot be a standing paragraph about "if this file is a notebook",
    which spends prefix tokens on a case that is usually absent and asks the model to
    decide something the path already settles.
    """
    if detect_language(file) != "notebook":
        return ""
    return load_prompt("notebook_note") + "\n"


def propose_file_features(
    file: str,
    chunks: list[dict],
    edges: list[dict],
    existing_titles: list[str],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
    why: list[dict] | None = None,
    brief: dict | None = None,
    doc_language: DocLanguage | None = None,
) -> list[NodeOp]:
    """One LLM call: propose a small coherent feature set for a single file.

    Cache-aligned (CACHE_BREAK markers in the template): the frozen instruction
    block + the grow-only titles list form the stable prefix; only the file
    block varies per call. Within a bootstrap wave every call shares the same
    titles snapshot, so the whole prefix is identical across the wave.
    Structured extraction → fast model tier by default.

    The answer goes past the prose gate before it is returned, which is where the
    style guide stops being advice. Bootstrap is the pass that writes the most
    prose in one go and the pass with the least context per node, so it is both the
    likeliest to slip into naming mechanisms and the cheapest place to catch it.

    ``why`` is this file's commit rationale (:func:`codoc.loop.why.commit_rationales`).
    It goes in the volatile tail with the file itself — it is per-file by
    construction, and putting it in the prefix would break the wave's shared
    cache for no benefit. At bootstrap this is the *only* why-evidence there is:
    nobody has edited the tree yet, so there are no directives and no recorded
    rationale to fall back on.

    A notebook gets one added block (:func:`_notebook_note`), in the volatile tail for
    the same reason ``why`` is: it is decided by the file. Without it this pass reads a
    notebook as a script that happens to have long strings in it, and makes three
    mistakes it cannot make on a `.py` file — it paraphrases sentences the AUTHOR wrote,
    it collapses the sections the author named into one node under the coarse-grouping
    rule, and it reports the shell lines codoc commented out as code somebody disabled.
    """
    # Split the raw template FIRST, then substitute per segment — substituted
    # values are repo-derived and may contain a literal marker.
    prefix_tpls, volatile_tpl = split_prompt(
        load_prompt("bootstrap_file", doc_language=doc_language))
    kwargs = dict(
        repo_name=repo_name,
        file=file,
        notebook_note=_notebook_note(file),
        chunks=json.dumps(chunks, indent=2, sort_keys=True, ensure_ascii=False),
        edges=json.dumps(edges, indent=2, sort_keys=True, ensure_ascii=False),
        existing_titles="\n".join(f"- {t}" for t in existing_titles) or "(none yet)",
        brief=format_brief(brief),
        why=(json.dumps(why, indent=2, sort_keys=True, ensure_ascii=False) if why
             else "(no commit history recorded for this file)"),
    )
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)

    # A repair is this same call with the critique appended to the VOLATILE tail,
    # so the wave's shared cache prefix stays byte-identical and the retry pays for
    # the critique alone.
    def ask(extra: str = "") -> list[NodeOp]:
        return _ops_from(run_agent(volatile + extra, config or fast_llm_config(),
                                   prefix_parts=prefix_parts))

    return _gated(ask(), ask, doc_language=doc_language)


def propose_settings_features(
    file: str,
    chunks: list[dict],
    readers: list[dict],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
    brief: dict | None = None,
    doc_language: DocLanguage | None = None,
) -> list[NodeOp]:
    """One LLM call: place a settings file's sections on the features that read it.

    A separate call from :func:`propose_file_features`, and separate because the
    answer has a different shape. That pass is asked what a file is FOR and returns
    new nodes; the right answer here is usually no new node at all — the decision in
    `[periods]` belongs to the feature whose code reads it, and what the tree is
    missing is not a place to put the section but the VALUE in a description that
    already exists. So this pass returns `attach` plus `amend`, and mints a node only
    for a section nothing accounts for.

    ``readers`` carries each candidate feature's id, the symbols of its code that name
    this file, and its current description — the description because an amend that
    cannot see what it is amending either repeats it or throws it away.

    Runs after every code file, so those features and their prose exist to be cited.
    """
    prefix_tpls, volatile_tpl = split_prompt(
        load_prompt("bootstrap_settings", doc_language=doc_language))
    kwargs = dict(
        repo_name=repo_name,
        file=file,
        chunks=json.dumps(chunks, indent=2, sort_keys=True, ensure_ascii=False),
        readers=(json.dumps(readers, indent=2, sort_keys=True, ensure_ascii=False)
                 if readers else "(no feature in the tree reads this file)"),
        brief=format_brief(brief),
    )
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)

    def ask(extra: str = "") -> list[NodeOp]:
        return _ops_from(run_agent(volatile + extra, config or fast_llm_config(),
                                   prefix_parts=prefix_parts))

    return _gated(ask(), ask, doc_language=doc_language)


def propose_organization(
    features: list[dict],
    edges: list[dict],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
    flows: list[str] | None = None,
    brief: dict | None = None,
    doc_language: DocLanguage | None = None,
) -> list[NodeOp]:
    """One LLM call: group file-level features under broad theme parents.

    ``flows`` are the package's main call paths (:mod:`codoc.loop.surface`).
    Coupling counts alone can only produce clusters — "these two call each other
    a lot" — and a top level built from clusters reads as a filing system. The
    paths say which features participate in one operation and in what order,
    which is what lets a theme be a stage of the work instead of a bucket.
    """
    template = load_prompt("bootstrap_org", doc_language=doc_language)
    prompt = format_prompt(
        template,
        repo_name=repo_name,
        features=json.dumps(features, indent=2, ensure_ascii=False),
        edges=json.dumps(edges, indent=2, ensure_ascii=False),
        flows="\n".join(f"- {f}" for f in flows) if flows
              else "(no call paths could be derived)",
        brief=format_brief(brief),
    )

    def ask(extra: str = "") -> list[NodeOp]:
        return _ops_from(run_agent(prompt + extra, config))

    # The pass where the altitude rule earns its keep: a theme is the first thing a
    # reader meets and the one node most likely to be written in the vocabulary of
    # the code underneath it.
    return _gated(ask(), ask, doc_language=doc_language)
