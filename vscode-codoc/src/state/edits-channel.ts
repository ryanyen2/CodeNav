/**
 * edits-channel.ts — the host's side of `.codoc/edits.json` (pure, testable).
 *
 * The annotation channel that carries authorship across the tree.codoc boundary:
 *   • `edits`   — per-feature "this settle was authored by X in mode Y" notes.
 *     The host APPENDS one per changed feature right before writing tree.codoc;
 *     Loop B drains them and stamps the matching user ops' ledger events
 *     (no annotation → defaults to human/pen). `suggestion_id` links a settle
 *     that applied a doc-ahead suggestion so the queued directive can carry it
 *     as `caused_by`.
 *   • `intents` — the LIVE doc-ahead suggestions (the doc-wins hold set). Owned
 *     by the host: rewritten wholesale from the current DocFile suggestions on
 *     every suggestion mutation; the Python loops only read it.
 *   • `cancellations` — realize-WITHDRAWALS (U6): feature ids whose queued
 *     directive the human cancelled. Loop B drains them and prunes the matching
 *     directive from the queue (releasing the hold); the prose is kept.
 *   • `steers` — one-shot inline-comment notes (U2b): with the host no longer
 *     writing tree.codoc, a `> …` comment is handed to Loop B here instead of the
 *     text round-trip; Loop B drains each into a STEER directive once.
 *
 * Mirrors codoc/loop/edits.py (schema version 1). File I/O lives in the host
 * (tree-editor.ts) — this module is pure data so vitest can pin the contract.
 */
import type { Suggestion } from './suggestion-model';

export interface EditAnnotation {
    feature_id: string;
    fields: string[];           // ["title"] | ["description"] | both
    actor: string;              // "human" | agent id
    mode: string;               // "pen" | "suggest"
    suggestion_id?: string;     // the doc-ahead suggestion this settle applied
    ts: number;                 // unix ms
}

export interface IntentEntry {
    id: string;                 // suggestion id
    feature_id: string;
    actor: string;
    ts: number;                 // unix ms
    /** The suggested text — present only for the field(s) the suggestion changes.
     *  A payload-carrying intent is APPLIED by Loop B's intent drain (the
     *  agent-side "apply"; the human's only verb is Withdraw, which removes the
     *  intent before the drain). Absent field = no change ("" = clear). */
    title?: string;
    description?: string;
}

/** A realize-WITHDRAWAL (U6): the human cancelled feature `feature_id`'s queued
 *  directive. Loop B drains these and prunes the matching directive from the queue
 *  (releasing the doc-wins hold); the committed prose is kept. */
export interface CancellationEntry {
    feature_id: string;
    ts: number;                 // unix ms
}

/** A one-shot inline-comment STEER (U2b): once the host stopped writing tree.codoc,
 *  a `> …` comment can't ride the text round-trip, so the webview hands it here.
 *  Loop B drains it once → a STEER directive. */
export interface SteerEntry {
    feature_id: string;
    text: string;               // the note (commentNoteText: `re "…": body`)
    comment_id: string;         // the doc thread id
    ts: number;                 // unix ms
}

/** A suggesting-mode DRAFT hold (U3/U4): the webview is holding feature `feature_id`'s
 *  code-implying edit as a draft — its realize directive stays held (out of realize.md /
 *  the agent) until the human hands off. The loop derives each directive's handed_off
 *  from this set every pass; removing a fid (hand-off) releases it. */
export interface DraftEntry {
    feature_id: string;
}

export interface EditsFile {
    version: 1;
    edits: EditAnnotation[];
    intents: IntentEntry[];
    /** Pending realize-withdrawals (U6). Omitted when empty (matches the Python
     *  writer, which only emits the key when non-empty). */
    cancellations?: CancellationEntry[];
    /** Pending one-shot comment steers (U2b). Omitted when empty. */
    steers?: SteerEntry[];
    /** Held suggesting-mode drafts (U3/U4). Omitted when empty → no holds → the daemon
     *  realizes code-implying edits immediately (today's behavior). */
    drafts?: DraftEntry[];
}

export function emptyEditsFile(): EditsFile {
    return { version: 1, edits: [], intents: [] };
}

export function parseEditsFile(json: unknown): EditsFile {
    if (!json || typeof json !== 'object') return emptyEditsFile();
    const o = json as Record<string, unknown>;
    const file: EditsFile = {
        version: 1,
        edits: Array.isArray(o.edits) ? (o.edits as EditAnnotation[]) : [],
        intents: Array.isArray(o.intents) ? (o.intents as IntentEntry[]) : [],
    };
    if (Array.isArray(o.cancellations)) file.cancellations = o.cancellations as CancellationEntry[];
    if (Array.isArray(o.steers)) file.steers = o.steers as SteerEntry[];
    if (Array.isArray(o.drafts)) file.drafts = o.drafts as DraftEntry[];
    return file;
}

/** Set the held-draft feature-id set wholesale (U3/U4). Pure. A code-implying draft edit
 *  adds the fid; "hand to agent" clears it (or removes the handed ids). An empty set is
 *  normalized to an absent `drafts` key so the file matches the Python omit-when-empty
 *  shape and a no-drafts file is byte-identical to before the feature. */
export function setDrafts(file: EditsFile, featureIds: readonly string[]): EditsFile {
    const seen = new Set<string>();
    const drafts: DraftEntry[] = [];
    for (const id of featureIds) {
        if (id && !seen.has(id)) { seen.add(id); drafts.push({ feature_id: id }); }
    }
    const next = { ...file };
    if (drafts.length) next.drafts = drafts;
    else delete next.drafts;
    return next;
}

/** Append a realize-withdrawal for `featureId` (deduped). Pure — returns a new
 *  EditsFile the host persists; Loop B drains the `cancellations` list. */
export function appendCancellation(file: EditsFile, featureId: string, ts: number): EditsFile {
    const cancellations = (file.cancellations ?? []).filter(c => c.feature_id !== featureId);
    cancellations.push({ feature_id: featureId, ts });
    return { ...file, cancellations };
}

/** Append a one-shot comment steer (U2b), replacing any prior steer for the same
 *  thread (an edit re-hands the latest note). Pure — Loop B drains the list once. */
export function appendSteer(
    file: EditsFile, entry: { feature_id: string; text: string; comment_id: string; ts: number },
): EditsFile {
    const steers = (file.steers ?? []).filter(s => s.comment_id !== entry.comment_id);
    steers.push(entry);
    return { ...file, steers };
}

/** A minimal parsed-feature shape (matches tree-model's ParsedFeature fields we need). */
export interface FeatureText {
    id: string | null;
    title: string;
    description: string;
}

/**
 * Per-feature annotations for a settle: which existing features' title and/or
 * description differ between the previous and next canonical text. New features
 * (no prior fid) need no annotation — an ADD op defaults to human/pen anyway.
 */
export function annotationsForSettle(
    prev: FeatureText[],
    next: FeatureText[],
    opts: { actor: string; mode: string; ts: number; suggestionId?: string },
): EditAnnotation[] {
    const before = new Map(prev.filter(f => f.id).map(f => [f.id as string, f]));
    const out: EditAnnotation[] = [];
    for (const f of next) {
        if (!f.id) continue;
        const b = before.get(f.id);
        if (!b) continue;
        const fields: string[] = [];
        if (b.title !== f.title) fields.push('title');
        if (b.description !== f.description) fields.push('description');
        if (!fields.length) continue;
        out.push({
            feature_id: f.id, fields, actor: opts.actor, mode: opts.mode,
            ...(opts.suggestionId ? { suggestion_id: opts.suggestionId } : {}),
            ts: opts.ts,
        });
    }
    return out;
}

/** Rewrite the intents list from the current persisted doc-ahead suggestions —
 *  the host owns this list, so wholesale replacement keeps it exactly in sync
 *  (created → added; withdrawn/applied/auto-cleared → removed → hold releases).
 *  Each intent carries the suggested text for the field(s) the suggestion
 *  changes; Loop B's intent drain applies it (the agent-side "apply"). */
export function intentsFromSuggestions(suggestions: Suggestion[], ts: number): IntentEntry[] {
    const out: IntentEntry[] = [];
    for (const s of suggestions) {
        if (s.direction !== 'doc-ahead' || !s.featureId) continue;
        const entry: IntentEntry = { id: s.id, feature_id: s.featureId, actor: s.originRole || 'human', ts };
        if ((s.titleNew ?? '') !== (s.titleOld ?? '') && s.titleNew != null) entry.title = s.titleNew;
        if ((s.descNew ?? '') !== (s.descOld ?? '') && s.descNew != null) entry.description = s.descNew;
        out.push(entry);
    }
    return out;
}
