/**
 * settlement.ts — one model for every "this span is not settled yet" mark.
 *
 * ## What was wrong
 *
 * Six decoration families independently answered the same question about the same
 * character range — *whose claim is this, and how far along is it?* — and each one
 * answered it with its own baseline, its own diff granularity, and its own hue:
 *
 *   captured-decorations   client diff vs a local baseline   word      blue underline
 *   hold-decorations       the daemon's hold set             node      sage chip
 *   agent-proposals        sidecar proposals → engine marks  sentence  ins/del, per-role tint
 *   ghost rows             sidecar proposals → widget        node      dimmed italic block
 *   auto-edit-decorations  sidecar auto_edits                sentence  rail + underline
 *   inline-blame           the revisions window              span      per-author underline
 *
 * Because they were six layers and not one model they could not COMPOSE. A feature
 * that had been rewritten by the loop, then edited by its author, then proposed
 * against by an agent wore three marks that each claimed the whole paragraph, drawn
 * in whatever order the extension list happened to register. "Which one wins" was
 * settled by z-index and by ad-hoc stand-down flags (`getLocallyEdited`) bolted onto
 * whichever layer noticed the collision last. And none of them could say the thing a
 * reader most needs after an agent works: *this sentence was planned, and what got
 * built differs from the plan here.*
 *
 * ## The model
 *
 * Every unsettled span is one CLAIM: a range, a CHANNEL (who is ahead), and a STAGE
 * (how far along). Three channels, and they are orthogonal — a span can carry one
 * from each:
 *
 *   human — the author wrote it; the code does not say it yet.   → blue TEXT
 *   plan  — an agent proposes it; nothing has been built.        → faded GRAY text
 *   code  — it surfaced back from code that already exists.      → green/red BACKGROUND
 *
 * One visual axis per channel (colour / opacity / background) is what lets them stack
 * on the same words without a legend: planned text that the build then altered is gray
 * (still the plan's words) with a red ground under the part that did not survive.
 *
 * ## Where the coordinates come from
 *
 * The three channels do NOT share a baseline, and pretending they did is what forced
 * the old stand-down flags. They share an ORIGIN — the projection the daemon wrote —
 * and each is a diff on one side of it:
 *
 *      code.prev ──diff──▶ projected ──materialize──▶ planned ──type──▶ live
 *                 (code)               (plan)                  (human)
 *
 *   • code  claims are computed against `projected` and MAPPED FORWARD into live
 *     coordinates. A human edit that lands on top of a code-surfaced span truncates
 *     it — correctly: those words are the author's now, not the codebase's.
 *   • plan  claims are the materialized proposal's own ins/del runs (agent-proposals
 *     puts the plan's words INTO the doc; they are not a widget beside it).
 *   • human claims are `planned → live`, so the author's typing is never confused
 *     with the plan text it was typed around.
 *
 * ## Nobody has to decide
 *
 * There is no forced verdict and there must not be one — an author who ignores a
 * proposal has still told you something. A claim is SUPERSEDED when a newer change
 * to the same feature arrives on top of it: the newer layer's base is the document as
 * it now reads, plan text and all, which is exactly "assume they accepted what is on
 * screen". `rebase()` is that rule, and it is the only drop rule that needs to fire
 * without a click. The explicit ones (accept, reject, commit, realize) are ordinary.
 *
 * Pure — no DOM, no TipTap, no vscode. `settlement.test.ts` drives it directly.
 */
import { wordDiff, sentenceDiff, type DiffRun } from './doc-diff';

// ── the vocabulary ───────────────────────────────────────────────────────────

/** Who is ahead of whom. One visual axis each; see the header. */
export type Channel = 'human' | 'plan' | 'code';

/**
 * How far along a claim is. The stage is what separates "still yours to change" from
 * "already handed over", which is the only thing motion is spent on: `open` pulses,
 * nothing else does.
 */
export type Stage =
    /** human: typed here, not handed to the agent. */
    | 'open'
    /** human: handed off — the agent will make the code say it. */
    | 'committed'
    /** plan: proposed, awaiting a verdict (or awaiting nothing — see `rebase`). */
    | 'proposed'
    /** plan: accepted intent, nothing built yet. */
    | 'accepted'
    /** code: this is what the code actually says. */
    | 'landed';

/** What the claim did to the text. A `del` has no live text to cover, so it is drawn
 *  at a point, carrying the words it removed. */
export type Edit = 'add' | 'del';

/** Which editable block of a feature a claim sits in. Offsets are into that block's
 *  DISPLAY text, which is what the decoration layer maps onto doc positions. */
export type BlockRef =
    | { kind: 'title' }
    | { kind: 'para'; index: number };

export interface Claim {
    channel: Channel;
    stage: Stage;
    edit: Edit;
    block: BlockRef;
    /** Char offsets into the block's display text. `start === end` ⇒ a deletion point. */
    start: number;
    end: number;
    /** The words this claim removed — present on `del`, which has nothing left to cover. */
    removed?: string;
    /** The layer this claim belongs to: a store event id, a directive id, or the
     *  local-edit sentinel. Verdicts, drops and hover text all key on it. */
    layerId: string;
}

/** A feature's editable text, split the way the editor holds it. */
export interface FeatureText {
    title: string;
    paras: string[];
}

export const LOCAL_EDIT_LAYER = 'local';

// ── inputs ───────────────────────────────────────────────────────────────────

/** One channel's contribution for a single feature. Every field is optional because
 *  the common feature has none of them: a settled node produces no claims at all. */
export interface FeatureLayers {
    /** What the daemon last wrote — the shared origin of all three channels. */
    projected: FeatureText;
    /** What the editor now holds: `projected`, plus the plan text materialized into
     *  it, plus this host's own typing. */
    live: FeatureText;
    /** The doc as it read once the plan was materialized but before anyone typed.
     *  Absent ⇒ no plan is materialized, so it is `projected`. */
    planned?: FeatureText;
    /** The loop rewrote this description on its own authority; `prev` is what it
     *  displaced. The rewrite is ALREADY in `projected`. */
    code?: { layerId: string; prev: FeatureText };
    /** A materialized proposal's own runs, per block, in `planned` coordinates. */
    plan?: { layerId: string; stage: 'proposed' | 'accepted'; runs: BlockRuns[] };
    /** True once the author handed their edits off (⌘S) — flips `open` to `committed`
     *  without recomputing anything. */
    committed?: boolean;
}

/** A block's diff runs as materialized (plan channel). */
export interface BlockRuns {
    block: BlockRef;
    runs: DiffRun[];
}

// ── position mapping ─────────────────────────────────────────────────────────

/**
 * Which side of a same-position insertion an offset lands on. A span maps its START
 * with `AFTER` and its END with `BEFORE`, so foreign text inserted exactly at either
 * edge falls OUTSIDE the span. The span shrinks rather than swallowing words nobody
 * in this channel wrote — the conservative direction, and the one that keeps a plan's
 * mark off the sentence the author typed after it.
 */
export const BEFORE = -1;
export const AFTER = 1;
export type Assoc = typeof BEFORE | typeof AFTER;

/**
 * Map an offset in the OLD side of a diff to the corresponding offset in the NEW side.
 *
 * Returned as a closure over a prebuilt table rather than re-walking the runs per
 * query: a description with a dozen code-surfaced sentences asks this a few dozen
 * times per rebuild, and the walk is the expensive half.
 *
 * Two rules carry all the interesting cases:
 *
 *   • An offset inside a DELETED run maps to the point where that run was, so a span
 *     the human has since overwritten collapses to zero width and `claimsFor` drops
 *     it. That is intended, not degenerate: those words are theirs now.
 *   • An INSERTED run occupies no width on the old side, so an offset at its position
 *     is genuinely ambiguous and `assoc` is the answer. Without this, a plan span that
 *     ended at the end of a paragraph grew to cover whatever the author typed next —
 *     the surface attributing one party's sentence to another.
 */
export function forwardMap(runs: readonly DiffRun[]): (oldOff: number, assoc?: Assoc) => number {
    // Parallel arrays of run boundaries in old/new coordinates.
    const oldAt: number[] = [0];
    const newAt: number[] = [0];
    const kinds: DiffRun['t'][] = [];
    let o = 0, n = 0;
    for (const run of runs) {
        kinds.push(run.t);
        if (run.t !== 'ins') o += run.s.length;
        if (run.t !== 'del') n += run.s.length;
        oldAt.push(o);
        newAt.push(n);
    }
    const oldEnd = o, newEnd = n;
    return (oldOff: number, assoc: Assoc = BEFORE): number => {
        const off = oldOff < 0 ? 0 : oldOff > oldEnd ? oldEnd : oldOff;
        for (let i = 0; i < kinds.length; i++) {
            if (kinds[i] === 'ins') {
                // Zero-width on the old side: only a query sitting exactly on it can
                // see it, and only a BEFORE query stops short of it.
                if (off === oldAt[i] && assoc === BEFORE) return newAt[i];
                continue;
            }
            if (off >= oldAt[i + 1]) continue;
            return kinds[i] === 'del' ? newAt[i] : newAt[i] + (off - oldAt[i]);
        }
        return newEnd;
    };
}

/** A half-open range. */
export interface Span { start: number; end: number }

/**
 * Carry a whole SPAN across a diff, keeping only what actually survived.
 *
 * Mapping the two endpoints and calling the result a span is wrong in the case that
 * matters: an insertion in the MIDDLE of the span silently joins it, so the mark ends
 * up covering words the channel never wrote. This walks instead, emitting the new-side
 * range of every `same` region the span overlaps — deleted text contributes nothing,
 * inserted text is somebody else's and is stepped over. Adjacent survivors that are
 * contiguous on the new side coalesce, so an untouched span comes back as one piece.
 */
export function mapSpan(runs: readonly DiffRun[], start: number, end: number): Span[] {
    const out: Span[] = [];
    let o = 0, n = 0;
    for (const run of runs) {
        if (run.t === 'ins') { n += run.s.length; continue; }
        const oEnd = o + run.s.length;
        if (run.t === 'same') {
            const from = Math.max(start, o), to = Math.min(end, oEnd);
            if (from < to) {
                const s = n + (from - o), e = n + (to - o);
                const last = out[out.length - 1];
                if (last && last.end === s) last.end = e;
                else out.push({ start: s, end: e });
            }
            n += run.s.length;
        }
        o = oEnd;
    }
    return out;
}

/** Total covered length — how much of a span survived a mapping. */
function covered(spans: readonly Span[]): number {
    return spans.reduce((t, s) => t + (s.end - s.start), 0);
}

// ── building claims ──────────────────────────────────────────────────────────

/**
 * Spans of a diff, in NEW coordinates.
 *
 * A deletion becomes a zero-width point carrying its words, because there is nothing
 * left to underline — "I don't think" → "I| think" is the case that taught this.
 * Insertions carry their range.
 */
function spansOf(runs: readonly DiffRun[]): { edit: Edit; start: number; end: number; removed?: string }[] {
    const out: { edit: Edit; start: number; end: number; removed?: string }[] = [];
    let at = 0;
    for (const run of runs) {
        if (run.t === 'same') { at += run.s.length; continue; }
        if (run.t === 'ins') { out.push({ edit: 'add', start: at, end: at + run.s.length }); at += run.s.length; continue; }
        out.push({ edit: 'del', start: at, end: at, removed: run.s });
    }
    return out;
}

/** Blocks a feature's two texts share, aligned by index. A block that exists on one
 *  side only still yields a pair (the missing side is ''), so a whole added or removed
 *  paragraph produces claims instead of being silently skipped. */
function alignedBlocks(a: FeatureText, b: FeatureText): { block: BlockRef; a: string; b: string }[] {
    const out: { block: BlockRef; a: string; b: string }[] = [
        { block: { kind: 'title' }, a: a.title, b: b.title },
    ];
    const n = Math.max(a.paras.length, b.paras.length);
    for (let i = 0; i < n; i++) {
        out.push({ block: { kind: 'para', index: i }, a: a.paras[i] ?? '', b: b.paras[i] ?? '' });
    }
    return out;
}

/** The diff granularity for a block. Titles are short — a sentence diff of one would
 *  strike the whole thing to fix a word. Prose is judged by the claim, so it moves in
 *  sentences: a word diff of a rewritten paragraph shreds both versions into alternating
 *  fragments the reader has to reassemble before they can agree with either. */
function diffFor(block: BlockRef, channel: Channel): (o: string, n: string) => DiffRun[] {
    if (block.kind === 'title') return wordDiff;
    // The code channel reports what the CODEBASE now says; sub-sentence churn there is
    // noise, so it never goes finer than a sentence.
    return channel === 'code' ? sentenceDiff : (channel === 'human' ? wordDiff : sentenceDiff);
}

/**
 * Every unsettled claim on one feature, in live coordinates, ordered by channel so a
 * consumer that draws them in order gets code under plan under human — the stacking
 * the visual grammar assumes (background, then opacity, then colour).
 */
export function claimsFor(f: FeatureLayers): Claim[] {
    const planned = f.planned ?? f.projected;
    const out: Claim[] = [];

    // ── code: computed against `projected`, carried forward into live ────────
    if (f.code) {
        // The two hops between `projected` and `live`: materializing the plan, then
        // the author typing. Compose them so a code span lands where its words are now.
        for (const { block, a: prev, b: proj } of alignedBlocks(f.code.prev, f.projected)) {
            if (prev === proj) continue;
            const spans = spansOf(diffFor(block, 'code')(prev, proj));
            if (!spans.length) continue;
            const planRuns = diffFor(block, 'plan')(textAt(f.projected, block), textAt(planned, block));
            const humanRuns = diffFor(block, 'human')(textAt(planned, block), textAt(f.live, block));
            const point = (off: number): number =>
                forwardMap(humanRuns)(forwardMap(planRuns)(off, BEFORE), BEFORE);
            for (const s of spans) {
                if (s.edit === 'del') {
                    out.push({ channel: 'code', stage: 'landed', edit: 'del', block, start: point(s.start), end: point(s.start), removed: s.removed, layerId: f.code.layerId });
                    continue;
                }
                // ALL OR NOTHING, and deliberately so. The code channel reports what
                // the codebase says, at the granularity of a sentence. The moment the
                // author edits inside that sentence it is no longer the sentence the
                // report was about, and marking the surviving fragment green would
                // point at words ("The uploader ") that carry none of the claim. A
                // plan gets the opposite treatment below, for the opposite reason.
                const hops = mapSpan(planRuns, s.start, s.end)
                    .flatMap(x => mapSpan(humanRuns, x.start, x.end));
                if (covered(hops) !== s.end - s.start) continue;
                for (const h of hops) {
                    out.push({ channel: 'code', stage: 'landed', edit: 'add', block, start: h.start, end: h.end, layerId: f.code.layerId });
                }
            }
        }
    }

    // ── plan: the materialized proposal's own runs, carried through typing ───
    if (f.plan) {
        for (const { block, runs } of f.plan.runs) {
            const humanRuns = diffFor(block, 'human')(textAt(planned, block), textAt(f.live, block));
            const toLive = forwardMap(humanRuns);
            for (const s of spansOf(runs)) {
                if (s.edit === 'del') {
                    const at = toLive(s.start, BEFORE);
                    out.push({ channel: 'plan', stage: f.plan.stage, edit: 'del', block, start: at, end: at, removed: s.removed, layerId: f.plan.layerId });
                    continue;
                }
                // SPLIT, never voided. A proposal is text you are meant to edit in
                // place before accepting it, so typing inside one is ordinary use —
                // and the mark has to survive that, tightened around the author's
                // words rather than swallowing them or vanishing.
                for (const h of mapSpan(humanRuns, s.start, s.end)) {
                    if (h.start >= h.end) continue;
                    out.push({ channel: 'plan', stage: f.plan.stage, edit: 'add', block, start: h.start, end: h.end, layerId: f.plan.layerId });
                }
            }
        }
    }

    // ── human: everything between the planned document and what is on screen ─
    const stage: Stage = f.committed ? 'committed' : 'open';
    for (const { block, a: base, b: live } of alignedBlocks(planned, f.live)) {
        if (base === live) continue;
        for (const s of spansOf(diffFor(block, 'human')(base, live))) {
            out.push({ channel: 'human', stage, edit: s.edit, block, start: s.start, end: s.end, removed: s.removed, layerId: LOCAL_EDIT_LAYER });
        }
    }

    return out;
}

/** A block's text out of a FeatureText. */
function textAt(t: FeatureText, block: BlockRef): string {
    return block.kind === 'title' ? t.title : (t.paras[block.index] ?? '');
}

// ── superseding: the drop rule nobody has to click ───────────────────────────

/**
 * Fold every claim into the text as it now reads and start over.
 *
 * This is what happens when the author does not answer. They kept typing, or the loop
 * ran again, or the agent came back with something else — and any of those is a
 * statement about the old claim: it is no longer the thing being decided. The next
 * round's baseline is the document ON SCREEN, plan text and all.
 *
 * Modelled as data rather than as a mutation so the caller can hold the two baselines
 * (what the daemon last wrote, what the author was last shown) without this module
 * knowing where either is stored.
 */
export function rebase(live: FeatureText): FeatureText {
    return { title: live.title, paras: [...live.paras] };
}

/**
 * Which layers a new payload retires.
 *
 * A layer is dropped when its own resolution arrives (accepted / rejected / realized),
 * OR when it is no longer offered — the daemon stopped shipping it, which is how a
 * proposal that got applied, withdrawn, or superseded by a later pass disappears. The
 * caller passes what is still on offer; everything else is history.
 */
export function droppedLayers(
    known: ReadonlySet<string>, stillOffered: ReadonlySet<string>,
): Set<string> {
    const out = new Set<string>();
    for (const id of known) if (id !== LOCAL_EDIT_LAYER && !stillOffered.has(id)) out.add(id);
    return out;
}
