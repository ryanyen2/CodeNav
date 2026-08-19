/**
 * comment-model.ts — inline comments: an author-side steering note anchored to a
 * span of prose, resolved by the LLM later (pure, testable; no vscode / DOM).
 *
 * A comment is the span-anchored sibling of the node-level `> …` steering note
 * (codoc/codoc_file/parse.py): selecting text and commenting is the same gesture
 * as typing `> …` under a feature, but the selected snippet rides along as context
 * for the agent. We DON'T invent a new backend channel — a comment serializes to a
 * `> …` line under its feature in `tree.codoc`, and Loop B's existing step-2.7
 * drain turns it into a `STEER FEATURE` directive. The line *disappearing* from the
 * text (Loop B consumed it on re-render) is the authoritative "agent took it" signal.
 *
 * Threads live host-side in `tree.doc.json` (DocFile.comments), like doc-ahead
 * suggestions. The doc carries only a `comment` mark (threadId) as the visual
 * anchor; the body + lifecycle live here. Two pure operations cover the loop:
 *   • injectComments — splice each OPEN thread's `> …` line into rendered tree.codoc
 *     (idempotent: an already-present line round-trips → no write loop).
 *   • reconcileComments — fold the freshly-parsed text back into the thread store:
 *     harvest raw-editor `> …` lines, mark a serialized-then-vanished thread drained,
 *     drop threads whose feature is gone.
 */
import type { ParsedFeature } from './tree-model';
import { REF_RE_SOURCE, type PMNode } from './pm-doc';

/** `resolved` is reached two ways and means the same thing to a reader: the author
 *  closed the thread, or the directive it produced landed. */
export type CommentStatus = 'open' | 'sent' | 'resolved';

/** What a comment asks to change. `code` (the default) steers the implementation and
 *  leaves the author's prose alone — the historic steer. `both` additionally asks for
 *  the description to be brought in line, for when the change alters what the feature is
 *  FOR and a description still describing the old behaviour is the next reader's bug. */
export type CommentScope = 'code' | 'both';

export interface CommentThread {
    /** Stable id, also the `comment` mark's threadId (the doc-side visual anchor). */
    id: string;
    /** Target feature (its ⟨f-id⟩); null = a brand-new heading not yet minted —
     *  held (not serialized) until the id lands, so we never emit an unanchored note. */
    featureId: string | null;
    /** The selected snippet the comment is about — context for the agent + the UI
     *  ("commenting on …"). Single-lined + truncated when it rides into the note. */
    anchorText: string;
    /** The note itself — what the agent should address. May be multi-line. */
    body: string;
    /** open = awaiting the agent; sent = Loop B drained it (line gone from text). */
    status: CommentStatus;
    /** Authoring role (for ink); defaults to 'human'. */
    author: string;
    createdAt: number;
    /** The host has written this thread's `> …` line into tree.codoc at least once.
     *  Distinguishes "open, never written" (keep, will emit) from "open, drained"
     *  (line vanished → mark sent), closing the resurrection race. */
    serialized?: boolean;
    /** A TRANSIENT consult attachment (U6) — a bug screenshot the author dropped on
     *  this thread. `ref` is the stored attachment path the host wrote under
     *  `.codoc/media/`; `kind` names the CONSULT plugin (`screenshot`). It rides the
     *  steer and is consumed once by realization — never a durable block. */
    media?: { kind: string; ref: string };
    // ── what makes a comment a unit of requested WORK (W8) ────────────────────
    /** `file::symbol` (or bare `file`) targets this note is about — they become the
     *  directive's `Edit only:` scope. Seeded from the citations inside the commented
     *  span, because the tree already says which code its prose is about: comment on a
     *  sentence that cites `[handle](codoc:upload.py#handle)` and the note is scoped to
     *  that, with nothing to pick. Empty ⇒ the whole feature, which is what a note
     *  attached to nothing in particular actually means. */
    codeRefs?: string[];
    /** Code only, or code AND the description (see `CommentScope`). Absent ⇒ `code`. */
    scope?: CommentScope;
    /** The realize directive this note became, stamped by the daemon. Its presence is
     *  what lets a thread say "this landed" and offer the code it produced. */
    directiveId?: string;
}

const ANCHOR_MAX = 60;

/** `[label](codoc:file#symbol)` citations inside a span, as `file::symbol` targets.
 *
 *  This is the whole "specify WHICH code" mechanism, and it needed no new UI: a codoc
 *  description cites its code inline, so the sentence an author selects to comment on
 *  usually already names what they mean. Selecting prose that cites nothing yields
 *  nothing, and the note falls back to the feature's full scope — which is the honest
 *  reading of a comment that pointed at no code in particular.
 *
 *  Deduped, order-preserving; a bare `codoc:file` ref (no `#symbol`) yields the file. */
export function codeRefsIn(text: string): string[] {
    const re = new RegExp(REF_RE_SOURCE, 'g');
    const out: string[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(text ?? '')) !== null) {
        const target = m[3] ? `${m[2]}::${m[3]}` : m[2];
        if (target && !out.includes(target)) out.push(target);
    }
    return out;
}

/** Mint a thread id. Prefixed `cm-` so it never collides with feature/event ids.
 *  Used for webview-authored comments (one mint per user action). */
export function mintCommentId(now: number, salt = ''): string {
    return 'cm-' + now.toString(36) + (salt ? '-' + salt : '');
}

/** Deterministic id for a harvested raw-editor `> …` note — derived from the
 *  feature + body so re-harvesting the same note yields the SAME id (no churn, no
 *  re-mint each payload). Prefixed `cm-h-`. */
export function harvestCommentId(featureId: string, body: string): string {
    let h = 5381;
    const s = featureId + '\x00' + body;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return 'cm-h-' + (h >>> 0).toString(36);
}

/** Collapse a snippet to a single trimmed line, truncated — safe to embed in a
 *  one-line `> re "…"` lead-in without breaking the round-trip. */
function oneLine(s: string): string {
    const flat = s.replace(/\s+/g, ' ').trim();
    return flat.length > ANCHOR_MAX ? flat.slice(0, ANCHOR_MAX - 1) + '…' : flat;
}

/**
 * The exact note text a thread emits into `tree.codoc` (without the `> ` prefix).
 * The anchored snippet rides as a `re "…":` lead-in so the agent knows WHICH span
 * the note is about — the whole point of a span comment. This is also the value
 * `reconcileComments` matches against the parsed `> …` runs, so emit + parse agree.
 */
export function commentNoteText(t: CommentThread): string {
    const body = t.body.trim();
    const anchor = oneLine(t.anchorText);
    return anchor ? `re "${anchor}": ${body}` : body;
}

/** Threads whose `> …` line should currently be present in the text: open, on a
 *  real feature. (A null-fid thread is held until its feature is minted.) */
function emittable(threads: CommentThread[]): CommentThread[] {
    return threads.filter(t => t.status === 'open' && t.featureId);
}

/** Group the emittable threads' note lines by feature id (document order kept). */
export function commentsByFid(threads: CommentThread[]): Map<string, string[]> {
    const out = new Map<string, string[]>();
    for (const t of emittable(threads)) {
        const note = commentNoteText(t);
        if (!note.trim()) continue;
        const arr = out.get(t.featureId!) ?? [];
        arr.push(note);
        out.set(t.featureId!, arr);
    }
    return out;
}

const HEADING_RE = /^(\s*)[-~]\s+.*⟨(f-[0-9a-f]+)⟩\s*$/;

/** One note → its `> …` lines at the feature's indent + 4 (a multi-line body
 *  becomes a contiguous `>` run, which parse.py rejoins into one comment). */
function noteLines(note: string, indent: string): string[] {
    const pad = indent + '    ';
    return note.split('\n').map(l => `${pad}> ${l}`);
}

/**
 * Splice each open thread's `> …` line into canonical `tree.codoc` text, at the end
 * of its feature's block (after the prose, before the block-separating blank).
 *
 * Reconstructs canonical spacing (one blank line between feature blocks, trailing
 * `rstrip + "\n"`) so the result is byte-stable: a note that's ALREADY in the text
 * round-trips to itself (renderTreeFromDoc drops it → we re-add the same line),
 * which keeps a no-op settle a no-op. Headings without a ⟨f-id⟩ (brand-new) and
 * features with no comments pass through untouched.
 */
export function injectComments(text: string, byFid: Map<string, string[]>): string {
    if (byFid.size === 0) return text;
    const lines = text.split('\n');

    // Group lines into [heading, ...body] blocks; anything before the first heading
    // is a preamble passed through verbatim. EVERY feature line (with or without a
    // ⟨f-id⟩) starts a new block — a brand-new fid-less heading is its own block and
    // is simply never injected into (no fid to match).
    interface Block { fid: string | null; lines: string[]; }
    const isHeading = (l: string): boolean => /^\s*[-~]\s+\S/.test(l);
    const preamble: string[] = [];
    const blocks: Block[] = [];
    let cur: Block | null = null;
    for (const line of lines) {
        if (isHeading(line)) {
            const m = HEADING_RE.exec(line);
            cur = { fid: m ? m[2] : null, lines: [line] };
            blocks.push(cur);
        } else if (cur) {
            cur.lines.push(line);
        } else {
            preamble.push(line);
        }
    }

    const rendered: string[] = [];
    for (const b of preamble) if (b.trim()) rendered.push(b);

    for (const block of blocks) {
        // Drop any existing `> …` lines (comments are OWNED by the store and re-emitted
        // below) so the function is idempotent even on already-injected text — a no-op
        // settle never grows a second copy of a note.
        const body = block.lines.filter((l, i) => i === 0 || !l.trim().startsWith('>'));
        while (body.length && body[body.length - 1].trim() === '') body.pop();
        rendered.push(...body);
        const notes = block.fid ? byFid.get(block.fid) : undefined;
        if (notes && notes.length) {
            const indent = (/^(\s*)/.exec(block.lines[0]) ?? ['', ''])[1];
            // A blank line BETWEEN notes keeps them distinct `>` runs (parse.py: a
            // blank ends a run, and a comment "owns" that break). Adjacent lines
            // would merge into one comment — collapsing two threads into one.
            notes.forEach((note, i) => {
                if (i > 0) rendered.push('');
                rendered.push(...noteLines(note, indent));
            });
        }
        rendered.push(''); // one blank line after every feature block
    }

    return rendered.join('\n').replace(/\s+$/, '') + '\n';
}

export interface ReconcileResult {
    threads: CommentThread[];
    /** True when a thread changed (status flip / harvest / drop) — caller re-persists. */
    changed: boolean;
}

/**
 * Fold freshly-parsed `tree.codoc` back into the thread store:
 *   • a stored OPEN thread whose feature is gone        → dropped
 *   • a stored OPEN thread whose `> …` line is present  → marked serialized (in text)
 *   • a stored OPEN thread serialized then now ABSENT   → marked `sent` (Loop B drained)
 *   • a `sent` thread is dropped once the realize cycle settles (status `in_sync`)
 *   • a parsed `> …` line with no matching thread       → harvested (raw-editor authored)
 *
 * `mintId` makes harvested ids without reaching for a clock inside the merge.
 */
export function reconcileComments(
    features: ParsedFeature[],
    stored: CommentThread[],
    opts: { inSync: boolean },
): ReconcileResult {
    const byId = new Map<string, ParsedFeature>();
    for (const f of features) if (f.id) byId.set(f.id, f);
    const noteSet = (fid: string): Set<string> =>
        new Set((byId.get(fid)?.comments ?? []).map(c => c.text));

    const out: CommentThread[] = [];
    const claimed = new Set<string>(); // "fid\x00note" already owned by a kept thread
    let changed = false;

    for (const t of stored) {
        // sent threads linger only through the realize cycle; clear at in_sync.
        if (t.status === 'sent') {
            if (opts.inSync) { changed = true; continue; }
            out.push(t);
            continue;
        }
        if (!t.featureId) { out.push(t); continue; } // held until minted
        if (!byId.has(t.featureId)) { changed = true; continue; } // feature gone → drop

        const note = commentNoteText(t);
        const inText = noteSet(t.featureId).has(note);
        if (inText) {
            claimed.add(t.featureId + '\x00' + note);
            if (!t.serialized) { out.push({ ...t, serialized: true }); changed = true; }
            else out.push(t);
        } else if (t.serialized) {
            out.push({ ...t, status: 'sent' }); // was in text, now drained
            changed = true;
        } else {
            out.push(t); // created, not yet written — host will emit it
        }
    }

    // Harvest raw-editor `> …` runs that no thread accounts for.
    for (const f of features) {
        if (!f.id) continue;
        for (const c of f.comments) {
            const key = f.id + '\x00' + c.text;
            if (claimed.has(key)) continue;
            claimed.add(key);
            out.push({
                id: harvestCommentId(f.id, c.text),
                featureId: f.id,
                anchorText: '',
                body: c.text,
                status: 'open',
                author: 'human',
                createdAt: 0,
                serialized: true, // already in the text
            });
            changed = true;
        }
    }

    return { threads: out, changed };
}

/**
 * Re-anchor null-fid threads: a comment authored on a brand-new heading (fid not
 * yet minted) is held with `featureId: null` and never serialized. Once the
 * feature is minted, its anchor `comment` mark sits under the now-fid'd heading —
 * recover the fid by walking the doc so the thread can finally emit its `> …` line
 * (otherwise it's a zombie: a marker that never reaches the agent). Returns the
 * same array (changed:false) when no null-fid thread could be resolved.
 */
export function reanchorComments(
    doc: PMNode,
    threads: CommentThread[],
): { threads: CommentThread[]; changed: boolean } {
    if (!threads.some(t => !t.featureId && t.status === 'open')) return { threads, changed: false };
    // map a comment threadId → the fid of its enclosing feature heading
    const fidByThread = new Map<string, string>();
    let curFid: string | null = null;
    const scan = (run: PMNode): void => {
        for (const m of run.marks ?? []) {
            if (m.type !== 'comment') continue;
            const tid = (m.attrs as { threadId?: string } | undefined)?.threadId;
            if (tid && curFid) fidByThread.set(String(tid), curFid);
        }
    };
    for (const block of doc.content ?? []) {
        if (block.type === 'featureHeading') curFid = (block.attrs as { fid?: string } | undefined)?.fid ?? null;
        for (const run of block.content ?? []) scan(run);
    }
    let changed = false;
    const out = threads.map(t => {
        if (t.featureId || t.status !== 'open') return t;
        const fid = fidByThread.get(t.id);
        if (!fid) return t;
        changed = true;
        return { ...t, featureId: fid };
    });
    return { threads: out, changed };
}

/**
 * Drop `comment` marks whose threadId is no longer a live thread (resolved, drained,
 * or feature-gone) so `tree.doc.json` doesn't accumulate dead anchors and the
 * store-driven decoration has nothing stale to find. Returns the same doc object
 * when nothing changed (cheap identity check for callers).
 */
export function stripOrphanComments(doc: PMNode, liveIds: Set<string>): PMNode {
    let mutated = false;
    const visit = (n: PMNode): PMNode => {
        let node = n;
        if (n.marks && n.marks.some(m => m.type === 'comment'
            && !liveIds.has(String((m.attrs as { threadId?: string } | undefined)?.threadId)))) {
            mutated = true;
            node = { ...n, marks: n.marks.filter(m => m.type !== 'comment'
                || liveIds.has(String((m.attrs as { threadId?: string } | undefined)?.threadId))) };
        }
        if (node.content) {
            const content = node.content.map(visit);
            if (content.some((c, i) => c !== node.content![i])) node = { ...node, content };
        }
        return node;
    };
    const out = visit(doc);
    return mutated ? out : doc;
}


// ── the store's copy (W8) ────────────────────────────────────────────────────

/** The daemon-written comment threads, read off the sidecar `comments` slice.
 *
 *  This is the durable half. A thread here has survived the tab that authored it, knows
 *  which directive it produced, and can say whether that directive landed — none of
 *  which the host's in-memory copy could ever report. */
export function storedThreads(sidecar: { comments?: Record<string, unknown[]> }): CommentThread[] {
    const out: CommentThread[] = [];
    for (const rows of Object.values(sidecar?.comments ?? {})) {
        for (const raw of rows ?? []) {
            const r = raw as Record<string, unknown>;
            const id = typeof r.id === 'string' ? r.id : '';
            const featureId = typeof r.feature_id === 'string' ? r.feature_id : '';
            if (!id || !featureId) continue;
            const status = r.status === 'sent' || r.status === 'resolved' ? r.status : 'open';
            out.push({
                id,
                featureId,
                anchorText: typeof r.anchor_text === 'string' ? r.anchor_text : '',
                body: typeof r.body === 'string' ? r.body : '',
                status,
                author: typeof r.author === 'string' ? r.author : 'human',
                // The store keeps an HLC; the UI wants milliseconds for its relative
                // times. The wall clock is the HLC's leading field.
                createdAt: hlcMs(typeof r.created_at === 'string' ? r.created_at : ''),
                serialized: true,
                codeRefs: Array.isArray(r.code_refs) ? r.code_refs.map(String) : undefined,
                scope: r.scope === 'both' ? 'both' : undefined,
                directiveId: typeof r.directive_id === 'string' ? r.directive_id : undefined,
                media: typeof r.media_ref === 'string' && r.media_ref
                    ? { kind: 'screenshot', ref: r.media_ref } : undefined,
            });
        }
    }
    return out;
}

function hlcMs(at: string): number {
    const head = (at ?? '').split('-')[0];
    const n = Number(head);
    return head && Number.isFinite(n) ? n : 0;
}

/**
 * The store's threads with this host's un-drained ones layered over them.
 *
 * LOCAL WINS on id collision, and that direction is the load-bearing part: between
 * authoring a note and the daemon's next pass, the store's copy is either absent or one
 * revision behind, and preferring it would make a comment blink out of the margin the
 * moment it was written. Everything the store knows and the local copy does not —
 * whether the directive landed, which one it was — is carried across rather than lost,
 * so the merged thread is never a downgrade of either side.
 */
export function mergeThreads(stored: CommentThread[], local: CommentThread[]): CommentThread[] {
    const byId = new Map(stored.map(t => [t.id, t]));
    for (const t of local) {
        const prior = byId.get(t.id);
        byId.set(t.id, prior
            ? {
                ...t,
                // A resolved thread stays resolved: the local copy is a stale optimistic
                // 'sent' from before the close, and un-resolving it would put a closed
                // conversation back in the margin.
                status: prior.status === 'resolved' ? 'resolved' : t.status,
                directiveId: t.directiveId ?? prior.directiveId,
                codeRefs: t.codeRefs?.length ? t.codeRefs : prior.codeRefs,
                scope: t.scope ?? prior.scope,
                createdAt: prior.createdAt || t.createdAt,
            }
            : t);
    }
    return [...byId.values()].sort((a, b) => a.createdAt - b.createdAt);
}
