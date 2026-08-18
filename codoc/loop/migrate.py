"""One-time, idempotent reconcile for workspaces created before the
store-authoritative refactor (the U8 migration).

Two heals run together via :func:`migrate_workspace`, both safe to rerun:

1. **Comment migration** — the pre-refactor webview held inline comment threads
   only in ``tree.doc.json`` (``DocFile.comments``). U4 makes the daemon the sole
   writer of that file, so it stops carrying host-authored comments; any open
   thread that was never lifted into the store would vanish. This reads the
   *pre-existing* ``tree.doc.json`` content (before the daemon rebuilds it) and
   upserts each thread into the store ``comments`` table (U1). It is idempotent by
   thread id — a thread already in the store is skipped — so a file that was
   already daemon-rebuilt (no ``comments`` key) is a no-op.

2. **Duplicate-feature dedup** — the dropped-``localId`` re-mint bug produced N
   features sharing the same ``(_norm_title, parent_id)`` (e.g. 3–5 copies of one
   authored node). Converge each such group deterministically:

   - **Keep the binding-owner.** ``UNIQUE(file, symbol_path)`` means at most one
     duplicate holds bindings; that fid is the keeper and is never retired.
   - **Tiebreak (no duplicate has bindings):** keep the earliest ``created_at``
     HLC; on an exact tie, the lexicographically-smallest fid. (Earliest-created
     is the original authored node; the fid tiebreak only ever fires on identical
     timestamps and just makes the choice deterministic.)
   - Merge each husk's description onto the keeper **only when** the keeper's is
     empty or strictly shorter (never clobber a longer/edited keeper description).
   - Re-point the husks' marks and comments (``feature_id`` update) to the keeper.
   - ``retire_feature`` the binding-less husks.

   After this runs, a subsequent ``run_loop_b`` mints nothing new because the
   ``(_norm_title, parent_id)`` collision is gone.

3. **Tracked-exception heal** — ``.codoc/.gitignore`` ignores everything but a
   short allow-list, and is written once and never overwritten (a user may have
   customized it). So a workspace created before ``config.json`` joined that list
   would keep the authoring language *untracked*: the maintainer sets the tree to
   Chinese, commits, and the contributor who clones gets an English default and a
   daemon that writes English prose into a Chinese tree. Appending the missing
   exceptions is the whole fix, and it leaves any customization in place.

Wiring: the ``codoc migrate`` CLI subcommand and the watch-daemon startup both
call :func:`migrate_workspace` so existing workspaces self-heal on the next run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from codoc.codoc_file.doc_parse import doc_path
from codoc.loop.loop_b import _norm_title
from codoc.model.annotation import CommentStatus, CommentThread
from codoc.model.block import Provenance
from codoc.model.hlc import HLC
from codoc.store.db import Store, open_store


@dataclass
class MigrationResult:
    comments_migrated: int = 0
    comments_skipped: int = 0
    duplicate_groups: int = 0
    features_retired: int = 0
    marks_repointed: int = 0
    comments_repointed: int = 0
    blocks_repointed: int = 0
    children_reparented: int = 0
    gitignore_healed: list[str] = field(default_factory=list)
    commands_installed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        base = (
            f"{self.comments_migrated} comments migrated "
            f"({self.comments_skipped} already present), "
            f"{self.duplicate_groups} duplicate groups converged "
            f"({self.features_retired} husks retired)"
        )
        if self.gitignore_healed:
            base += f", .gitignore now tracks {', '.join(self.gitignore_healed)}"
        if self.commands_installed:
            base += f", installed {', '.join(self.commands_installed)}"
        return base

    def changed(self) -> bool:
        return bool(
            self.comments_migrated or self.duplicate_groups or self.features_retired
            or self.gitignore_healed or self.commands_installed
        )


# -- comment migration ----------------------------------------------------

def _read_doc_comments(codoc_dir: str | Path) -> list[dict]:
    """Extract the raw ``DocFile.comments`` array from a pre-existing
    ``tree.doc.json``. Tolerant: a missing/corrupt file, a bare ProseMirror doc,
    or a daemon-rebuilt file with no ``comments`` key all yield ``[]``."""
    path = doc_path(codoc_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    comments = data.get("comments")
    return [c for c in comments if isinstance(c, dict)] if isinstance(comments, list) else []


def _provenance(author: str | None) -> Provenance:
    try:
        return Provenance(author or "human")
    except ValueError:
        return Provenance.HUMAN


def _status(raw: str | None) -> CommentStatus:
    # The TS side only ever stored 'open' | 'sent'; map the rest to OPEN.
    try:
        return CommentStatus(raw or "open")
    except ValueError:
        return CommentStatus.OPEN


def migrate_comments(store: Store, codoc_dir: str | Path, result: MigrationResult) -> None:
    """Lift host-authored ``tree.doc.json`` comment threads into the store
    ``comments`` table. Idempotent by id (skip a thread already present)."""
    raw = _read_doc_comments(codoc_dir)
    if not raw:
        return
    existing = {c.id for c in store.all_comments()}
    for c in raw:
        cid = c.get("id")
        if not cid:
            continue
        if cid in existing:
            result.comments_skipped += 1
            continue
        # The TS thread shape (comment-model.CommentThread) carries no anchor
        # offsets — the doc-side `comment` mark was the visual anchor. We preserve
        # the body + provenance + lifecycle; anchors default to 0 (a feature-level
        # note), and the anchorText snippet rides into the body if separate.
        feature_id = c.get("featureId") or c.get("feature_id") or ""
        if not feature_id:
            # A null-fid thread was a held note on an un-minted heading — it has no
            # durable home in the store; skip it (it never anchored to a feature).
            result.comments_skipped += 1
            continue
        thread = CommentThread(
            id=cid,
            feature_id=feature_id,
            body=c.get("body") or "",
            author=_provenance(c.get("author")),
            status=_status(c.get("status")),
            anchor_start=int(c.get("anchor_start") or c.get("anchorStart") or 0),
            anchor_end=int(c.get("anchor_end") or c.get("anchorEnd") or 0),
            media_ref=(c.get("media") or {}).get("ref", "") if isinstance(c.get("media"), dict) else (c.get("media_ref") or ""),
        )
        store.upsert_comment(thread)
        existing.add(cid)
        result.comments_migrated += 1


# -- duplicate-feature dedup ----------------------------------------------

def _pick_keeper(group, bound_ids: set[str]) -> str:
    """Choose the surviving fid for a ``(_norm_title, parent_id)`` group.

    Keep the binding-owner (``UNIQUE(file, symbol_path)`` → at most one in a
    group). If none holds bindings, keep the earliest ``created_at``; on an exact
    HLC tie, the lexicographically-smallest fid."""
    owners = [f for f in group if f.id in bound_ids]
    if owners:
        # At most one by the unique constraint; if somehow more, prefer earliest.
        return min(owners, key=lambda f: (f.created_at, f.id)).id
    return min(group, key=lambda f: (f.created_at, f.id)).id


def dedup_features(store: Store, result: MigrationResult) -> None:
    """Converge duplicate ``(_norm_title, parent_id)`` feature groups onto a single
    keeper (binding-owner preferred). Never retires a binding-owner."""
    feats = store.list_features()  # live only
    bound_ids = store.bound_feature_ids()
    groups: dict[tuple[str, str | None], list] = {}
    for f in feats:
        groups.setdefault((_norm_title(f.title), f.parent_id), []).append(f)

    for _key, group in groups.items():
        if len(group) < 2:
            continue
        result.duplicate_groups += 1
        keeper_id = _pick_keeper(group, bound_ids)
        keeper = store.get_feature(keeper_id)
        if keeper is None:
            continue
        for husk in group:
            if husk.id == keeper_id:
                continue
            if husk.id in bound_ids:
                # Defensive: never retire a binding-owner. The unique constraint
                # makes this unreachable for a normalized-title group, but if the
                # data is dirtier than expected, keep both rather than lose code.
                result.notes.append(
                    f"kept bound duplicate {husk.id} (not the chosen keeper {keeper_id})"
                )
                continue
            # Merge description only if the keeper's is empty/shorter (don't clobber).
            husk_desc = (husk.description or "").strip()
            keep_desc = (keeper.description or "").strip()
            if husk_desc and len(husk_desc) > len(keep_desc):
                keeper.description = husk.description
                keeper.updated_at = HLC.now()
                store.upsert_feature(keeper)
                keep_desc = husk_desc
            # Re-point rich-state rows to the keeper.
            for m in store.marks_for_feature(husk.id):
                m.feature_id = keeper_id
                m.updated_at = HLC.now()
                store.upsert_mark(m)
                result.marks_repointed += 1
            for c in store.comments_for_feature(husk.id):
                c.feature_id = keeper_id
                c.updated_at = HLC.now()
                store.upsert_comment(c)
                result.comments_repointed += 1
            # Re-point the husk's typed-media blocks to the keeper (same as marks /
            # comments) so retiring the husk doesn't strand a diagram/latex/image the
            # user authored on the duplicate. Upsert by the block's stable id (KTD8).
            for b in store.blocks_for_feature(husk.id):
                b.feature_id = keeper_id
                b.updated_at = HLC.now()
                store.upsert_block(b)
                result.blocks_repointed += 1
            # Reparent the husk's LIVE children onto the keeper before retiring it, so
            # the subtree isn't orphaned / re-rooted (a child whose parent_id pointed at
            # the husk would dangle once the husk is hidden). list_features() is live-only,
            # so retired children are left as-is.
            for child in feats:
                if child.parent_id == husk.id:
                    child.parent_id = keeper_id
                    child.updated_at = HLC.now()
                    store.upsert_feature(child)
                    result.children_reparented += 1
            store.retire_feature(husk.id)
            result.features_retired += 1


# -- entry point ----------------------------------------------------------

def heal_gitignore(codoc_dir: str | Path, result: MigrationResult) -> None:
    """Append any missing tracked-file exception to ``.codoc/.gitignore``.

    Only ever adds ``!name`` lines, and only for names not already mentioned
    anywhere in the file — so a user who deliberately commented one out, or wrote
    their own pattern that covers it, is left alone. A missing file is not created
    here: that is ``bootstrap._write_codoc_gitignore``'s job, and a workspace with
    no ``.gitignore`` at all is one where the user removed it on purpose.
    """
    from codoc.loop.bootstrap import TRACKED_IN_CODOC

    gi = Path(codoc_dir) / ".gitignore"
    if not gi.exists():
        return
    try:
        text = gi.read_text(encoding="utf-8")
    except OSError:
        return
    missing = [n for n in TRACKED_IN_CODOC if n not in text]
    if not missing:
        return
    suffix = "" if text.endswith("\n") else "\n"
    try:
        gi.write_text(text + suffix + "".join(f"!{n}\n" for n in missing),
                      encoding="utf-8")
    except OSError:
        return
    result.gitignore_healed = missing


def heal_plugin_commands(codoc_dir: str | Path, result: MigrationResult) -> None:
    """Install slash commands that shipped after this workspace was wired up.

    ``codoc install-hooks`` writes one file per command and nothing re-ran it on
    upgrade, so a workspace wired before ``/codoc:ask`` shipped kept ``/codoc:plan``
    and ``/codoc:sync`` for good — the command existed in the package, in the
    docs, and in the MCP, and simply did not exist for anybody who had already
    installed codoc. Nobody would think to re-run an installer to get a feature
    they read about in a changelog.

    ADDS ONLY, and only where the plugin is already installed. A workspace with
    no ``.claude/commands/codoc/`` never wanted the plugin and this is not the
    place to decide otherwise; and a command already on disk is left alone,
    because overwriting is ``install-hooks``' job, not a heal's.
    """
    from codoc.agent.install_hooks import _plugin_dir

    dest_ns = Path(codoc_dir).parent / ".claude" / "commands" / "codoc"
    src_ns = _plugin_dir() / "commands" / "codoc"
    if not dest_ns.is_dir() or not src_ns.is_dir():
        return
    added: list[str] = []
    for cmd in sorted(src_ns.glob("*.md")):
        dest = dest_ns / cmd.name
        if dest.exists():
            continue
        try:
            dest.write_text(cmd.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            continue
        added.append(f"/codoc:{cmd.stem}")
    result.commands_installed = added


def migrate_workspace(codoc_dir: str | Path) -> MigrationResult:
    """Run the one-time heals against a ``.codoc`` dir. Idempotent and safe to
    rerun: a clean (already-converged, no stale comments) workspace is a no-op.

    Order matters: comment migration reads the *pre-existing* ``tree.doc.json``
    (it must run before the daemon rebuilds that file from the store in U4), and
    dedup re-points comments, so comments land first. The ``.gitignore`` heal is
    independent of the store, so it runs outside the store context."""
    result = MigrationResult()
    with open_store(codoc_dir) as store:
        migrate_comments(store, codoc_dir, result)
        dedup_features(store, result)
    heal_gitignore(codoc_dir, result)
    heal_plugin_commands(codoc_dir, result)
    return result
