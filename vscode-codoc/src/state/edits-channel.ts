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
 *   • `commands` — identity-keyed authored edits (U3): the EXPLICIT op the webview
 *     emits (add/set_title/set_description/move/retire) instead of letting Loop B
 *     infer it from a doc diff. Drained + applied via apply_op (KTD3), idempotent
 *     on the store's applied-command ledger (KTD8).
 *
 * Mirrors codoc/loop/edits.py (schema version 2 — the U3 commands channel). File I/O
 * lives in the host (tree-editor.ts) — this module is pure data so vitest can pin the
 * contract.
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
    /** A TRANSIENT consult attachment (U6) — a bug screenshot dropped in the
     *  thread. `media` is the stored attachment ref (a `.codoc/media/…` path);
     *  `media_kind` names the CONSULT plugin (`screenshot`). Consumed with the
     *  steer, never persisted as a block. Omitted when the comment has no media. */
    media?: string;
    media_kind?: string;
    ts: number;                 // unix ms
}

/** A suggesting-mode DRAFT hold (U3/U4): the webview is holding feature `feature_id`'s
 *  code-implying edit as a draft — its realize directive stays held (out of realize.md /
 *  the agent) until the human hands off. The loop derives each directive's handed_off
 *  from this set every pass; removing a fid (hand-off) releases it. */
export interface DraftEntry {
    feature_id: string;
}

/** A hand-off request: the human explicitly chose to realize feature `feature_id`'s
 *  held draft (commit / ⌘S, or the per-feature hand-off action). The positive realize
 *  signal in the held-draft model — Loop B flips the matching held directive to
 *  handed_off and (re)builds realize.md. One-shot (drained by the loop). */
export interface HandoffEntry {
    feature_id: string;
}

/** A typed-media block edit (v6) handed to Loop B for `lower` dispatch. Keyed by
 *  the STABLE block id (KTD8) — identity is never inferred from content, so a move
 *  (ord change) emits NO entry and a delete+undo nets to nothing. `action`:
 *  `edit`/`add` dispatch `lower`; `remove` drops the projection (NEVER the code). */
export interface BlockEditEntry {
    block_id: string;
    feature_id: string;
    kind: string;
    action: 'edit' | 'add' | 'remove';
    content: string;        // new content (empty for remove)
    prev_content: string;   // content before the edit (the lower delta)
    ts: number;             // unix ms
}

/** The kinds an identity-keyed command (U3) may carry — mirrors Python
 *  `edits.COMMAND_KINDS`. `set_title`/`set_description` are description-level
 *  (SUGGEST-eligible); `add`/`move`/`retire` are structural (HANDOFF-gated, KTD10). */
export type CommandKind = 'add' | 'set_title' | 'set_description' | 'move' | 'retire';

/** An identity-keyed authored command (U3 / KTD3) — the EXPLICIT op the webview emits
 *  instead of letting Loop B infer it from a doc diff. Mirrors the Python `Command`
 *  dataclass. `id` is the idempotency key (KTD8) recorded in the store's applied-command
 *  ledger; `localId` correlates an `add`'s minted fid back to the in-progress node;
 *  `payload` carries title/description/parentId per kind. */
export interface CommandEntry {
    id: string;
    kind: CommandKind;
    feature_id?: string;        // target (empty for `add`, which mints)
    local_id?: string;          // webview client-side node id for `add` (minted-fid correlation)
    /** The value this command REPLACES, as this editor last knew the store to hold it.
     *  It is the common ancestor the daemon merges from when the feature has moved
     *  since (see loop_b._resolve_content): edits on different lines both land,
     *  and where they truly overlap a person's edit beats an agent's while two
     *  peers' edits go up for review rather than one silently erasing the other.
     *  Full text, not a hash: the comparison then uses ONE normalizer — the
     *  daemon's — so there is no TypeScript/Python hash parity to drift. */
    base_text?: string;
    /** The editing session that authored this command. The daemon uses it to tell a
     *  continuation of this editor's own work (base legitimately behind, because the
     *  projection has not caught up yet) from a genuine disagreement with someone
     *  else's write. */
    session?: string;
    payload?: {
        title?: string;
        description?: string;
        parent_id?: string | null;
        /** `add` only: `false` = an authored PLAN node (the `◇ plan` gesture), which is
         *  what makes the daemon mint a build directive for it
         *  (classify.edit_mints_directive: ADD mints iff `realized is False`). Omitted
         *  means realized — the NodeOp default — so an ordinary add is unchanged.
         *
         *  The field's absence is why the plan button was decorative: the flag lived on
         *  the heading, never crossed this channel, and every authored node reached the
         *  daemon as an ordinary feature. */
        realized?: boolean;
        /** Sibling anchors for `move` and `add` — the features the node landed
         *  between. Omitted means no opinion about order (appends). */
        after_id?: string;
        before_id?: string;
    };
}

export interface EditsFile {
    version: 2;
    edits: EditAnnotation[];
    intents: IntentEntry[];
    /** Pending identity-keyed authored commands (U3). Omitted when empty (Python
     *  omit-when-empty shape). Drained + applied by Loop B via apply_op (KTD3). */
    commands?: CommandEntry[];
    /** Pending realize-withdrawals (U6). Omitted when empty (matches the Python
     *  writer, which only emits the key when non-empty). */
    cancellations?: CancellationEntry[];
    /** Pending one-shot comment steers (U2b). Omitted when empty. */
    steers?: SteerEntry[];
    /** Held suggesting-mode drafts (U3/U4). Omitted when empty. In the held-draft model
     *  the daemon holds every doc AMEND by default; this set drives the "captured" UI. */
    drafts?: DraftEntry[];
    /** Pending typed-media block edits (v6). Omitted when empty. */
    block_edits?: BlockEditEntry[];
    /** Pending hand-off requests: feature ids the human EXPLICITLY chose to realize
     *  (commit / ⌘S). The POSITIVE realize signal in the held-draft model — Loop B
     *  flips the matching held directives to handed_off. One-shot (drained). Omitted
     *  when empty. */
    handoffs?: HandoffEntry[];
}

export function emptyEditsFile(): EditsFile {
    return { version: 2, edits: [], intents: [] };
}

export function parseEditsFile(json: unknown): EditsFile {
    if (!json || typeof json !== 'object') return emptyEditsFile();
    const o = json as Record<string, unknown>;
    const file: EditsFile = {
        version: 2,
        edits: Array.isArray(o.edits) ? (o.edits as EditAnnotation[]) : [],
        intents: Array.isArray(o.intents) ? (o.intents as IntentEntry[]) : [],
    };
    if (Array.isArray(o.commands)) file.commands = o.commands as CommandEntry[];
    if (Array.isArray(o.cancellations)) file.cancellations = o.cancellations as CancellationEntry[];
    if (Array.isArray(o.steers)) file.steers = o.steers as SteerEntry[];
    if (Array.isArray(o.drafts)) file.drafts = o.drafts as DraftEntry[];
    if (Array.isArray(o.block_edits)) file.block_edits = o.block_edits as BlockEditEntry[];
    if (Array.isArray(o.handoffs)) file.handoffs = o.handoffs as HandoffEntry[];
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

/** Append hand-off requests for the given feature ids (deduped, order-preserving) —
 *  the POSITIVE realize signal in the held-draft model. Pure; the host persists it and
 *  Loop B drains the `handoffs` list, flipping the matching held drafts to handed_off.
 *  An empty result normalizes to an absent `handoffs` key (Python omit-when-empty shape). */
export function appendHandoffs(file: EditsFile, featureIds: readonly string[]): EditsFile {
    const seen = new Set((file.handoffs ?? []).map(h => h.feature_id));
    const handoffs: HandoffEntry[] = [...(file.handoffs ?? [])];
    for (const id of featureIds) {
        if (id && !seen.has(id)) { seen.add(id); handoffs.push({ feature_id: id }); }
    }
    const next = { ...file };
    if (handoffs.length) next.handoffs = handoffs;
    else delete next.handoffs;
    return next;
}

/** Append a one-shot comment steer (U2b), replacing any prior steer for the same
 *  thread (an edit re-hands the latest note). Pure — Loop B drains the list once. */
export function appendSteer(file: EditsFile, entry: SteerEntry): EditsFile {
    const steers = (file.steers ?? []).filter(s => s.comment_id !== entry.comment_id);
    steers.push(entry);
    return { ...file, steers };
}

/** Append a typed-media block edit (v6). A pure reorder/move is NOT a block edit
 *  (it has no code effect), so the host only calls this for content edits, adds,
 *  and removes. A later edit to the same block supersedes the prior pending one
 *  (keyed by block_id) so iterating a block doesn't stack entries. Pure — Loop B
 *  drains the list once and dispatches `lower`. */
export function appendBlockEdit(
    file: EditsFile,
    entry: { block_id: string; feature_id: string; kind: string; action: 'edit' | 'add' | 'remove'; content?: string; prev_content?: string; ts: number },
): EditsFile {
    const block_edits = (file.block_edits ?? []).filter(b => b.block_id !== entry.block_id);
    block_edits.push({
        block_id: entry.block_id, feature_id: entry.feature_id, kind: entry.kind,
        action: entry.action, content: entry.content ?? '', prev_content: entry.prev_content ?? '',
        ts: entry.ts,
    });
    return { ...file, block_edits };
}

/** Append an identity-keyed authored command (U3). Replaces any prior pending command
 *  with the same `id` (idempotency key) so a re-emit supersedes rather than stacks.
 *  Pure — returns a new EditsFile the host persists; Loop B drains + applies the
 *  `commands` list via apply_op (KTD3), idempotent on the store ledger (KTD8). */
export function appendCommand(file: EditsFile, entry: CommandEntry): EditsFile {
    const commands = (file.commands ?? []).filter(c => c.id !== entry.id);
    commands.push(entry);
    return { ...file, commands };
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
