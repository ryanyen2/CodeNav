/**
 * revision-model.ts — the tree as it was, reconstructed in the editor (W8).
 *
 * The daemon ships `.codoc/revisions.json`: a bounded newest-first window of applied
 * events, each carrying the text it DISPLACED (`prev_title` / `prev_description` /
 * `prev_parent_id`, recorded at the Python write boundary). This module turns that into
 * a scrubbable history of the live document.
 *
 * ## Backwards, from what is already on screen
 *
 * Reconstruction runs BACKWARDS from the live projection rather than forwards from a
 * stored snapshot, and that is the whole design:
 *
 * - **It is local.** Dragging a timeline cannot afford a round trip per frame, and the
 *   webview has no request channel to the daemon anyway — it reads files. Undoing a
 *   handful of recorded fields is microseconds.
 * - **It costs what the change cost.** A revision is a paragraph, not a tree.
 * - **It starts from the truth.** The live document is authoritative and already in
 *   hand; a stored snapshot would be a second copy that can disagree with it.
 *
 * ## It says what it cannot reconstruct
 *
 * An event written before the `prev_*` fields existed records the new value and nothing
 * else. There is no backfill and there must not be one: showing today's words as though
 * they were Tuesday's is a version history that lies, which is strictly worse than one
 * that admits a gap. Such a feature lands in `Snapshot.unresolved`, and the surface says
 * so instead of drawing a confident diff over invented text.
 *
 * ## Moments, not events
 *
 * A raw event list is not a timeline. Bootstrap alone mints dozens of `add_node`s in one
 * second, and a single save can produce an amend plus three binding ops. Consecutive
 * events by the same actor, implementing the same directive, within a short gap are one
 * MOMENT — the unit a person actually means by "a version".
 *
 * No DOM, no vscode, no TipTap — pure, and unit-tested directly.
 */
import type { WarrantRow } from './bindings-model';
import { PMNode, blocksToDescriptionText, inlineRunsToText, NODE_FEATURE_HEADING, NODE_PARAGRAPH } from './pm-doc';

// ── the wire shape (mirrors codoc/loop/revisions.py) ─────────────────────────

/** One applied event. Every optional field is PRESENCE-keyed: absent means the op did
 *  not touch it, which `null` could not express (that would read as "cleared it"). */
export interface RevisionEntry {
    event_id: string;
    at: string;            // HLC string, lexicographically sortable
    kind: string;          // amend | add_node | move_node | retire_node | attach | detach
    feature_id: string;
    actor: string;
    mode: string;
    caused_by?: string;
    rationale?: string;
    /** The evidence the prose this op wrote rests on — see `WarrantRow`. */
    warrant?: WarrantRow[];
    /** What this op wrote. */
    title?: string;
    description?: string;
    /** What it displaced. */
    prev_title?: string;
    prev_description?: string;
    prev_written_by?: string;
    /** `parent_id` is the destination; `prev_parent_id` is where it came from, where
     *  `''` means "was a root" and absent means "not recorded". */
    parent_id?: string | null;
    prev_parent_id?: string;
    /** `file::symbol` strings for attach/detach — no prose, but this is what a reader
     *  means by "codoc bound this feature to that code". */
    bindings?: string[];
}

/** The directive a revision cites — where its WHY lives. */
export interface RevisionDirective {
    id: string;
    kind: string;
    feature_id: string;
    text: string;
    /** True once the queue closed on it (it is in `realized.jsonl`, not `realize.json`). */
    done: boolean;
    /** The captured author prompt behind it. */
    asked?: string;
    /** The coding session that prompt was typed in. */
    session_id?: string;
    /** HEAD when the directive was handed off — the "before" side of its code diff. */
    base_sha?: string;
    completed_at?: string;
}

export interface RevisionsFile {
    version: number;
    /** Newest first, matching every other feed the IDE consumes. */
    revisions: RevisionEntry[];
    directives: Record<string, RevisionDirective>;
    /** The window is full — the tree has history older than this file carries. */
    truncated?: boolean;
}

// ── moments ──────────────────────────────────────────────────────────────────

/** Events further apart than this are separate moments even from the same author.
 *  Two minutes is about the span in which a person still calls their own edits "the
 *  same change"; past it they remember them as two. */
export const MOMENT_GAP_MS = 120_000;

/** One scrubber stop: a run of events that happened together, by one author, for one
 *  reason. `id` is the newest event id in the run — stable across payload refreshes, so
 *  the scrubber does not jump when a pass appends. */
export interface Moment {
    id: string;
    /** HLC of the newest event in the run. */
    at: string;
    atMs: number;
    actor: string;
    /** The directive/event/suggestion every entry cites, or `''` when they differ. */
    causedBy: string;
    /** Newest first within the moment. */
    entries: RevisionEntry[];
    /** Features this moment touched, in first-seen order. */
    fids: string[];
}

export interface Timeline {
    /** OLDEST first — the scrubber reads left (past) to right (now). */
    moments: Moment[];
    directives: Record<string, RevisionDirective>;
    /** There is history older than the oldest moment here. */
    truncated: boolean;
}

/** Milliseconds encoded in an HLC (`<wall>-<logical>-<node>`, wall in ms); NaN when
 *  unparseable. Duplicated from blame-model deliberately: that module is about
 *  authorship, this one about time, and one shared 5-line parser between them would be
 *  a dependency neither needs. */
export function hlcMs(at: string): number {
    const s = at ?? '';
    if (!s) return NaN;              // `Number('')` is 0 — i.e. 1970, silently
    const dash = s.indexOf('-');
    const head = dash >= 0 ? s.slice(0, dash) : s;
    if (!head) return NaN;
    const n = Number(head);
    return Number.isFinite(n) ? n : NaN;
}

/** Group a newest-first revision window into oldest-first moments. */
export function buildTimeline(file: RevisionsFile | null | undefined): Timeline {
    const entries = [...(file?.revisions ?? [])];
    // The file is newest-first; moments read oldest-first, so walk it in reverse and
    // extend the run forward in time. Sorting is not needed — the daemon writes in `at`
    // order — but ties on a same-millisecond HLC would be arbitrary either way, and the
    // grouping window swallows them.
    const moments: Moment[] = [];
    for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i];
        const ms = hlcMs(e.at);
        const cause = e.caused_by ?? '';
        const open = moments.length ? moments[moments.length - 1] : null;
        const joins = open
            && open.actor === e.actor
            && open.causedBy === cause
            && Number.isFinite(ms) && Number.isFinite(open.atMs)
            && ms - open.atMs <= MOMENT_GAP_MS;
        if (joins && open) {
            open.entries.unshift(e);   // keep newest-first within the moment
            open.id = e.event_id;
            open.at = e.at;
            open.atMs = ms;
            if (e.feature_id && !open.fids.includes(e.feature_id)) open.fids.push(e.feature_id);
        } else {
            moments.push({
                id: e.event_id, at: e.at, atMs: ms, actor: e.actor, causedBy: cause,
                entries: [e], fids: e.feature_id ? [e.feature_id] : [],
            });
        }
    }
    return {
        moments,
        directives: file?.directives ?? {},
        truncated: !!file?.truncated,
    };
}

// ── snapshots ────────────────────────────────────────────────────────────────

export interface SnapshotFeature {
    fid: string;
    title: string;
    description: string;
    /** `null` = a root. */
    parentId: string | null;
}

export interface Snapshot {
    features: Map<string, SnapshotFeature>;
    /** Sibling ordering: every known fid, in the order the live document had them, with
     *  resurrected nodes slotted after their parent. Preorder is derived from this plus
     *  the parent links (`preorder`). */
    order: string[];
    /** Features whose text at this point could NOT be recovered — an op changed them
     *  without recording what it displaced. The surface must say so rather than draw a
     *  diff against the wrong words. */
    unresolved: Set<string>;
}

/** The live document, read as the snapshot reconstruction starts from.
 *
 *  Depth is encoded on the heading (`level`), not as parent links, so parentage is the
 *  nearest preceding heading one level shallower — the same rule `doc-layout` uses to
 *  flatten the tree into one article, read back. */
export function liveSnapshot(doc: PMNode | null | undefined): Snapshot {
    const features = new Map<string, SnapshotFeature>();
    const order: string[] = [];
    const blocks = doc?.content ?? [];
    // Stack of the most recent fid at each level, so a heading at level N takes the
    // last-seen level N-1 as its parent.
    const atLevel: string[] = [];
    let current: SnapshotFeature | null = null;
    let paras: PMNode[] = [];
    const flush = (): void => {
        if (current) current.description = blocksToDescriptionText(paras);
        paras = [];
    };
    for (const b of blocks) {
        if (b.type === NODE_FEATURE_HEADING) {
            flush();
            const attrs = (b.attrs ?? {}) as { fid?: string | null; level?: number };
            const fid = attrs.fid ?? '';
            if (!fid) { current = null; continue; }
            const level = Math.max(0, Number(attrs.level ?? 0) || 0);
            atLevel.length = level;                    // drop anything deeper
            atLevel[level] = fid;
            current = {
                fid,
                title: inlineRunsToText(b.content),
                description: '',
                parentId: level > 0 ? (atLevel[level - 1] ?? null) : null,
            };
            features.set(fid, current);
            order.push(fid);
        } else if (b.type === NODE_PARAGRAPH && current) {
            paras.push(b);
        }
    }
    flush();
    return { features, order, unresolved: new Set() };
}

function cloneSnapshot(s: Snapshot): Snapshot {
    const features = new Map<string, SnapshotFeature>();
    for (const [k, v] of s.features) features.set(k, { ...v });
    return { features, order: [...s.order], unresolved: new Set(s.unresolved) };
}

/** Undo ONE recorded event against a snapshot, in place.
 *
 *  Each branch answers the same question — what did this op destroy, and did it say so?
 *  Where it did not, the feature is marked unresolved rather than left showing text from
 *  a different day. */
function undo(snap: Snapshot, e: RevisionEntry): void {
    const fid = e.feature_id;
    if (!fid) return;
    switch (e.kind) {
        case 'amend': {
            const f = snap.features.get(fid);
            if (!f) return;
            if (e.title !== undefined) {
                if (e.prev_title !== undefined) f.title = e.prev_title;
                else snap.unresolved.add(fid);
            }
            if (e.description !== undefined) {
                if (e.prev_description !== undefined) f.description = e.prev_description;
                else snap.unresolved.add(fid);
            }
            return;
        }
        case 'add_node': {
            // Before this event the feature did not exist. Its own children were added
            // later and have already been undone by their own entries; anything still
            // parented here was moved in, and keeping it visible (re-parented to this
            // node's parent) beats dropping a subtree the ledger never said to drop.
            const f = snap.features.get(fid);
            if (!f) return;
            for (const other of snap.features.values()) {
                if (other.parentId === fid) other.parentId = f.parentId;
            }
            snap.features.delete(fid);
            const at = snap.order.indexOf(fid);
            if (at >= 0) snap.order.splice(at, 1);
            snap.unresolved.delete(fid);
            return;
        }
        case 'retire_node': {
            // Before the retire the node was live, with the text and parent the op
            // recorded. Without those it cannot be put back at all — a resurrected node
            // with no title is worse than an honest gap, so it stays absent.
            if (e.prev_title === undefined && e.prev_description === undefined) return;
            const parent = e.prev_parent_id === undefined
                ? null
                : (e.prev_parent_id || null);
            snap.features.set(fid, {
                fid,
                title: e.prev_title ?? '',
                description: e.prev_description ?? '',
                parentId: parent,
            });
            if (!snap.order.includes(fid)) {
                // Slot it after its parent so it lands inside the right subtree; a root
                // goes to the end. Sibling position within the parent is not recorded
                // anywhere (rank is computed at the write boundary), so this is a
                // deliberate, deterministic choice rather than a reconstruction.
                const anchor = parent ? snap.order.indexOf(parent) : -1;
                if (anchor >= 0) snap.order.splice(anchor + 1, 0, fid);
                else snap.order.push(fid);
            }
            if (e.prev_title === undefined || e.prev_description === undefined) {
                snap.unresolved.add(fid);
            }
            return;
        }
        case 'move_node': {
            const f = snap.features.get(fid);
            if (!f) return;
            if (e.prev_parent_id === undefined) snap.unresolved.add(fid);
            else f.parentId = e.prev_parent_id || null;
            return;
        }
        default:
            // attach / detach change code attribution, not the document. They are real
            // timeline entries — "codoc bound this to that code" — but undoing one
            // changes nothing a reader would see.
            return;
    }
}

/**
 * The tree as of the end of `moments[index]`.
 *
 * `index` is into `timeline.moments` (oldest first). `index === moments.length - 1` is
 * the live document unchanged; `index === -1` is the state before the oldest recorded
 * moment. Everything NEWER than the chosen moment is undone, newest first — the order
 * matters, because two amends to one feature only compose correctly unwound in reverse.
 */
export function snapshotAt(live: Snapshot, timeline: Timeline, index: number): Snapshot {
    const snap = cloneSnapshot(live);
    for (let m = timeline.moments.length - 1; m > index; m--) {
        for (const e of timeline.moments[m].entries) undo(snap, e);  // entries newest first
    }
    return snap;
}

/** Depth-first document order for a snapshot, as `{fid, level}` rows.
 *
 *  Roots and siblings follow `snap.order` (the live document's own sequence), so a
 *  reconstructed page reads in the order the reader remembers rather than in id order.
 *  A cycle — which the store forbids but a partial reconstruction could still produce —
 *  terminates instead of hanging: every fid is emitted at most once. */
export function preorder(snap: Snapshot): { fid: string; level: number }[] {
    const childrenOf = new Map<string | null, string[]>();
    for (const fid of snap.order) {
        const f = snap.features.get(fid);
        if (!f) continue;
        const key = f.parentId && snap.features.has(f.parentId) ? f.parentId : null;
        const bucket = childrenOf.get(key);
        if (bucket) bucket.push(fid);
        else childrenOf.set(key, [fid]);
    }
    const out: { fid: string; level: number }[] = [];
    const seen = new Set<string>();
    const walk = (parent: string | null, level: number): void => {
        for (const fid of childrenOf.get(parent) ?? []) {
            if (seen.has(fid)) continue;
            seen.add(fid);
            out.push({ fid, level });
            walk(fid, level + 1);
        }
    };
    walk(null, 0);
    return out;
}

// ── what changed at one moment ───────────────────────────────────────────────

/** One feature's before/after across a moment. `before === null` means the feature did
 *  not exist yet; `after === null` means it was retired. */
export interface FeatureChange {
    fid: string;
    before: SnapshotFeature | null;
    after: SnapshotFeature | null;
    /** True when the ledger did not record enough to state the "before" — the surface
     *  must say so rather than diff against the wrong words. */
    unresolved: boolean;
    /** The kinds of op that touched this feature in the moment. */
    kinds: string[];
}

/**
 * What `moments[index]` did, as a per-feature before/after.
 *
 * Computed as the difference between two reconstructions rather than by reading the
 * entries directly, so a moment containing three amends to one feature reports the one
 * change a reader perceives instead of three intermediate steps nobody saw.
 */
export function changesAt(live: Snapshot, timeline: Timeline, index: number): FeatureChange[] {
    const moment = timeline.moments[index];
    if (!moment) return [];
    const after = snapshotAt(live, timeline, index);
    const before = snapshotAt(live, timeline, index - 1);
    const kindsByFid = new Map<string, string[]>();
    for (const e of moment.entries) {
        if (!e.feature_id) continue;
        const ks = kindsByFid.get(e.feature_id);
        if (ks) { if (!ks.includes(e.kind)) ks.push(e.kind); }
        else kindsByFid.set(e.feature_id, [e.kind]);
    }
    const out: FeatureChange[] = [];
    for (const fid of moment.fids) {
        const b = before.features.get(fid) ?? null;
        const a = after.features.get(fid) ?? null;
        if (!b && !a) continue;   // touched only by a binding op on a since-removed node
        // Unresolved is checked BEFORE the equality shortcut, and the order is
        // load-bearing: an unrecoverable change is precisely the case where before and
        // after are identical *because the undo failed*. Reading that as "nothing
        // changed" would hide the one gap this model exists to admit.
        const unresolved = before.unresolved.has(fid) || after.unresolved.has(fid);
        out.push({ fid, before: b, after: a, unresolved, kinds: kindsByFid.get(fid) ?? [] });
    }
    return out;
}

/** Every file the moment's entries name, deduped, in first-seen order. What "the agent
 *  changed these files" is drawn from; the `file::symbol` suffix is dropped because a
 *  diff opens a file, not a symbol. */
export function filesTouched(moment: Moment): string[] {
    const out: string[] = [];
    for (const e of moment.entries) {
        for (const b of e.bindings ?? []) {
            const file = b.split('::')[0];
            if (file && !out.includes(file)) out.push(file);
        }
    }
    return out;
}
