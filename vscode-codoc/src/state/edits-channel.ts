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

export interface EditsFile {
    version: 1;
    edits: EditAnnotation[];
    intents: IntentEntry[];
    /** Pending realize-withdrawals (U6). Omitted when empty (matches the Python
     *  writer, which only emits the key when non-empty). */
    cancellations?: CancellationEntry[];
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
    return file;
}

/** Append a realize-withdrawal for `featureId` (deduped). Pure — returns a new
 *  EditsFile the host persists; Loop B drains the `cancellations` list. */
export function appendCancellation(file: EditsFile, featureId: string, ts: number): EditsFile {
    const cancellations = (file.cancellations ?? []).filter(c => c.feature_id !== featureId);
    cancellations.push({ feature_id: featureId, ts });
    return { ...file, cancellations };
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
