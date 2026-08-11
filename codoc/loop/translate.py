"""Migrate an existing tree into another authoring language.

Switching ``doc_language`` changes what codoc *originates* from that point on; it
deliberately does not touch prose already on the page (see :mod:`codoc.doclang`). So
a repo that has been running in English and now wants Chinese needs an explicit,
author-invoked conversion, which is this module.

It is the one operation in codoc that rewrites every description at once, so the
whole design is about being safe to run and honest afterwards:

* **Validation before application** (:func:`check_translation`). A translation that
  dropped a `codoc:` citation, a `**bold**` focus span, or an external link is
  REFUSED for that node rather than applied — each of those is a live channel
  (a binding reference, a `Focus:` directive line, a `Consult:` line), so losing one
  silently changes behavior rather than just wording.
* **Applied, not proposed.** A rewrite of the whole description would fail the amend
  gate on every node and land ~N proposals for an N-node tree, which nobody can
  review. The author asked for exactly this rewrite by running the command, so it
  applies — but only from a command, never as a side effect of the language switch.
* **The prior writer role is restored** (:func:`_apply_one`). ``apply_op`` reassigns
  ``feature_writers`` to whoever wrote last, so translating would re-stamp every
  human-authored node as loop-written — quietly dropping it from the strict
  ``PRESERVE_RATIO_HUMAN`` gate to the loose machine one, and inviting the loop to
  freely revise prose that is still the author's.
* **The old text survives** in the event ledger: an applied AMEND records
  ``prev_description`` + ``prev_written_by``, so ``codoc history <feature>`` shows
  exactly what each node said before. That is the undo story, and it is worth
  knowing about before running this on a tree whose ``tree.codoc`` is not committed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from codoc.agent.translate import propose_translations
from codoc.codoc_file.parse import extract_bold, extract_links, extract_refs
from codoc.doclang import (
    DocLanguage, detect_prose_language, norm_key, prose_letters,
    workspace_doc_language,
)
from codoc.model.event import ACTOR_LOOP, MODE_AUTO, NodeOp, NodeOpKind
from codoc.store.db import Store, open_store

_log = logging.getLogger(__name__)

TRANSLATE_SOURCE = "translate"

#: Features per LLM call. Small enough that one bad response costs little and
#: progress lands incrementally, large enough that the model sees sibling nodes and
#: keeps terminology consistent across them.
BATCH = 12


@dataclass
class Skipped:
    feature_id: str
    title: str
    reason: str


@dataclass
class TranslateResult:
    considered: int = 0
    translated: int = 0
    already: int = 0            # already in the target language — nothing to do
    calls: int = 0
    skipped: list[Skipped] = field(default_factory=list)
    # (title_before, title_after) for the first few, so --dry-run can show its work.
    preview: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.translated} translated"]
        if self.already:
            parts.append(f"{self.already} already in the target language")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        parts.append(f"{self.calls} LLM call(s)")
        return " · ".join(parts)


def check_translation(
    old_title: str,
    old_description: str,
    new_title: str,
    new_description: str,
    language: DocLanguage,
    *,
    taken_titles: frozenset[str] = frozenset(),
) -> str | None:
    """Why this translation must not be applied, or None when it is safe.

    Pure, so every rule is testable without an LLM. Ordered cheapest-first, and each
    one exists because of what silently breaks otherwise:

    - an empty title or description would blank an authored node;
    - a title that collides with a DIFFERENT live sibling makes two distinct features
      indistinguishable to the soft ``(normalized_title, parent_id)`` identity key —
      and ``migrate.dedup_features`` would then converge them, destroying one of two
      features that were never duplicates, only translated into the same words;
    - a lost ``codoc:`` citation is a lost binding reference — the ref registry marks
      it dead and the reader loses the one link to the code the claim is about;
    - a lost external link drops a ``Consult:`` line, so the realizing agent stops
      reading a page the author told it to read;
    - a lost ``**bold**`` span drops a ``Focus:`` line, silently demoting the part of
      the intent the author marked as most important;
    - prose that did not change script was not translated, and applying it would
      count a no-op as done and skip the node on the next run.
    """
    if not new_title.strip():
        return "empty title"
    if not new_description.strip():
        return "empty description"
    if norm_key(new_title) in taken_titles:
        return (f"the translated title collides with another feature under the same "
                f"parent ({new_title!r}) — two nodes sharing a title become one to "
                f"the dedup pass")

    old_refs = {(r.file, r.symbol) for r in extract_refs(old_description)}
    new_refs = {(r.file, r.symbol) for r in extract_refs(new_description)}
    if old_refs - new_refs:
        missing = ", ".join(f"{f}#{s}" for f, s in sorted(old_refs - new_refs))
        return f"dropped code citation(s): {missing}"

    old_urls = {link.url for link in extract_links(old_description)}
    new_urls = {link.url for link in extract_links(new_description)}
    if old_urls - new_urls:
        return f"dropped external link(s): {', '.join(sorted(old_urls - new_urls))}"

    old_bold, new_bold = extract_bold(old_description), extract_bold(new_description)
    if old_bold and not new_bold:
        return f"dropped the author's focus span(s): {', '.join(old_bold)}"

    # "Did it actually translate?" is asked of the DESCRIPTION, not the title: a
    # title can legitimately be a bare identifier that reads the same in any
    # language, while a description long enough to judge should have moved.
    if prose_letters(old_description) >= 12:
        got = detect_prose_language(new_description, language)
        if got.code != language.code:
            return f"came back in {got.name}, not {language.name}"
    return None


def _needs_translation(feature, language: DocLanguage) -> bool:
    """Whether this node's prose is not yet in ``language``.

    Judged on title+description together so a node with a translated title and an
    untranslated body still counts. Note the tension with the per-node rule that
    normally protects a deliberately-other-language node: THIS command is the
    author's explicit instruction to convert the tree, so a bulk run converts
    everything and `--dry-run` is how they check the list first.
    """
    text = f"{feature.title or ''}\n{feature.description or ''}"
    return detect_prose_language(text, language).code != language.code


def _apply_one(store: Store, feature, title: str, description: str,
               language: DocLanguage) -> None:
    """Apply one translation as an applied AMEND, preserving the writer role."""
    prior_writer, prior_role = store.feature_writer_info(feature.id)
    op = NodeOp(
        kind=NodeOpKind.AMEND,
        feature_id=feature.id,
        title=title,
        description=description,
        rationale=f"translated to {language.name} at the author's request "
                  f"(codoc translate); wording moved, claims unchanged",
    )
    from codoc.loop.apply import apply_op

    # applied=True explicitly: `should_auto_apply` would refuse every one of these
    # (a whole-description rewrite preserves nothing) and turn the migration into one
    # proposal per node. The author asked for the rewrite by running the command —
    # that is the difference between this and the loop editing prose on its own.
    apply_op(op, store, source=TRANSLATE_SOURCE, applied=True,
             actor=ACTOR_LOOP, mode=MODE_AUTO)
    # …but the LEDGER saying "the loop wrote this" must not become "the loop OWNS
    # this". Restoring the prior role keeps a human-authored node under the strict
    # preserve gate; without it, translating a tree would quietly license the loop to
    # rewrite every description in it.
    if prior_role:
        store.set_feature_writer(feature.id, prior_writer, prior_role)


def translate_tree(
    codoc_dir: str | Path,
    *,
    language: DocLanguage | None = None,
    dry_run: bool = False,
    limit: int = 0,
    repo_name: str = "codebase",
    config=None,
    propose=propose_translations,
    printer=None,
) -> TranslateResult:
    """Translate every node not already in ``language`` (default: the workspace's).

    Idempotent and resumable: selection is by *detected* language, so a rerun picks
    up only what is still untranslated — which is what makes a partial failure
    (a rate limit, an interrupted run) safe to simply re-run.
    """
    codoc_dir = Path(codoc_dir)
    say = printer or (lambda *_a, **_k: None)
    lang = language or workspace_doc_language(codoc_dir)
    res = TranslateResult()

    from codoc.loop.locks import loop_lock

    # Under the loop lock: this rewrites most of the store, and interleaving with a
    # daemon Loop A/B pass would let that pass read half-translated state.
    with loop_lock(codoc_dir), open_store(codoc_dir) as store:
        features = [f for f in store.list_features() if not f.retired]
        res.considered = len(features)
        pending = [f for f in features if _needs_translation(f, lang)]
        res.already = len(features) - len(pending)
        if limit > 0:
            pending = pending[:limit]

        # Live (normalized_title, parent_id) keys, so a translation cannot land on a
        # name another node already holds. Maintained as translations apply, which
        # also stops two nodes in one batch from translating to the same title —
        # a real risk, since sibling features often differ by a word that a shorter
        # language collapses.
        taken: dict[str | None, set[str]] = {}
        for f in features:
            taken.setdefault(f.parent_id, set()).add(norm_key(f.title))

        for start in range(0, len(pending), BATCH):
            batch = pending[start:start + BATCH]
            payload = [
                {"id": f.id, "title": f.title or "", "description": f.description or ""}
                for f in batch
            ]
            try:
                got = propose(payload, lang, repo_name=repo_name, config=config)
                res.calls += 1
            except Exception as exc:  # noqa: BLE001 — one bad batch must not sink the run
                _log.warning("codoc translate: batch failed (%s)", exc)
                for f in batch:
                    res.skipped.append(Skipped(f.id, f.title or "", f"batch failed: {exc}"))
                continue

            for f in batch:
                pair = got.get(f.id)
                if pair is None:
                    res.skipped.append(Skipped(f.id, f.title or "", "no translation returned"))
                    continue
                new_title, new_description = pair
                siblings = taken.setdefault(f.parent_id, set())
                why = check_translation(
                    f.title or "", f.description or "", new_title, new_description,
                    lang,
                    # This node's own current title is not a collision with itself.
                    taken_titles=frozenset(siblings - {norm_key(f.title)}),
                )
                if why:
                    res.skipped.append(Skipped(f.id, f.title or "", why))
                    continue
                siblings.discard(norm_key(f.title))
                siblings.add(norm_key(new_title))
                if len(res.preview) < 8:
                    res.preview.append((f.title or "", new_title))
                if not dry_run:
                    _apply_one(store, f, new_title, new_description, lang)
                res.translated += 1
            say(f"  … {res.translated}/{len(pending)}")

        if dry_run:
            return res

        # Re-render every derived artifact once, at the end: the whole tree moved, so
        # per-node renders would be pure waste, and the webview repaints from these.
        if res.translated:
            from codoc.codoc_file.render import write_tree
            from codoc.loop.loop_b import write_tree_doc
            from codoc.loop.status import refresh_status

            write_tree(store, codoc_dir)
            write_tree_doc(store, codoc_dir)
            refresh_status(codoc_dir, store)
    return res
