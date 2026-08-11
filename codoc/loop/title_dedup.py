"""Semantic (embedding) title dedup — D1 of docs/plans/2026-06-20-001.

The exact-string guard (``loop_a._norm_title`` + ``_unbound_features_by_title``)
folds a re-proposed node only when its title matches character-for-character after
normalization. A *paraphrased* duplicate — "Persist drafts" vs "Save draft edits"
— slips past it and mints a new node. This module adds a near-duplicate gate:
when a freshly-proposed node's title is embedding-close to an *adoptable*
(binding-less / unbound) existing feature, the caller folds (ATTACH) into that
node instead of minting a sibling.

It is **opt-in** (the embedder is heavier than the rest of Loop A, and the
threshold wants tuning against the real corpora before it ships on by default —
see the plan's Phase 3 note) and **fails safe**: any embedder error degrades to
"no semantic match", i.e. exactly today's behavior. Pure over an injected
``embed_fn`` so it is unit-testable with a deterministic fake.
"""
from __future__ import annotations

import math
import os
from typing import Callable

# Cosine-similarity floor for two titles to count as the same concept. Deliberately
# conservative (a high bar) so the guard only folds clear paraphrases, never merely
# related features — over-folding silently destroys a distinct node, the worse
# failure. Tunable; the default awaits a corpus eval (Phase 3).
DEFAULT_THRESHOLD = 0.82

# The env switch that turns the (off-by-default) semantic gate on in the
# production loop entrypoints. Tests inject an ``embed_fn`` directly and ignore it.
ENABLE_ENV = "CODOC_SEMANTIC_DEDUP"

EmbedFn = Callable[[list[str]], list[list[float]]]


def semantic_dedup_enabled() -> bool:
    """True when the operator opted into semantic title dedup for the live loop."""
    return os.environ.get(ENABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def make_loop_embedder(codoc_dir: str | None = None) -> EmbedFn | None:
    """The production ``embed_fn`` for the loop: a warm sentence-transformer (one
    model load per pass). Returns None — semantic dedup stays off — when the
    embedder package isn't installed or its config can't be built, so a missing
    optional dependency never breaks a loop pass.

    ``codoc_dir`` selects a model that can read this repo's titles. The default
    (``all-MiniLM-L6-v2``) is English-only, so on a Chinese tree it maps every
    title to near-noise and the gate silently folds nothing or, worse, folds two
    unrelated titles — a paraphrase gate that cannot read the language it is
    gating is not a conservative gate, it is a random one. An explicit
    ``CODOC_EMBEDDER_MODEL`` still wins; this only changes the default.
    """
    try:
        from codoc.config import get_embedder_config, make_embedder

        cfg = get_embedder_config()
        if not os.environ.get("CODOC_EMBEDDER_MODEL"):
            from codoc.doclang import embedder_model_for

            cfg = cfg.model_copy(update={"model": embedder_model_for(codoc_dir)})
        return make_embedder(cfg)
    except Exception:  # noqa: BLE001 — any embedder setup failure ⇒ feature off
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors in [-1, 1]; 0.0 for a degenerate vector.
    Pure-python (no numpy) — the vectors are small (a few hundred dims) and this
    keeps the gate dependency-light."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticTitleMatcher:
    """Precomputes the candidate titles' embeddings ONCE per pass, then answers
    "is this new title a near-duplicate of an adoptable node?" cheaply.

    ``candidates`` is ``[(title, feature_id)]`` for the live, still-unbound
    features a new node could fold into. Construction embeds them in a single
    ``embed_fn`` call; :meth:`best_match` embeds just the one query title. On ANY
    embedder error the matcher goes inert (``best_match`` returns None), so the
    caller falls through to the unchanged exact-string behavior."""

    def __init__(self, embed_fn: EmbedFn, candidates: list[tuple[str, str]],
                 *, threshold: float = DEFAULT_THRESHOLD):
        self._embed = embed_fn
        self._threshold = threshold
        self._fids: list[str] = []
        self._vecs: list[list[float]] = []
        if not candidates:
            return
        titles = [t for t, _ in candidates]
        try:
            self._vecs = embed_fn(titles)
            self._fids = [fid for _, fid in candidates]
        except Exception:  # noqa: BLE001 — inert on embedder failure
            self._fids, self._vecs = [], []

    @property
    def active(self) -> bool:
        return bool(self._fids)

    def best_match(self, title: str | None) -> str | None:
        """The feature id of the closest adoptable candidate whose cosine ≥ the
        threshold, or None (no confident paraphrase match → mint as before)."""
        if not self.active or not (title or "").strip():
            return None
        try:
            q = self._embed([title])[0]
        except Exception:  # noqa: BLE001
            return None
        best_fid: str | None = None
        best_score = self._threshold
        for fid, v in zip(self._fids, self._vecs):
            s = _cosine(q, v)
            if s >= best_score:
                best_fid, best_score = fid, s
        return best_fid
