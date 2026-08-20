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
 * The human channel is INK ONLY — no diff view. What you removed is not a thing you
 * need shown back to you; you are the one who removed it, and a ghost of your own
 * deleted words is a surface narrating your typing. The other two channels report
 * removals because there somebody ELSE took the words out. The claims still exist in
 * both cases (a deletion-only edit must still reach the marker); only the drawing
 * differs, and it differs in one place.
 *
 * One visual axis per channel (colour / opacity / background) is what lets them stack
 * on the same words without a legend: planned text that the build then altered is gray
 * (still the plan's words) with a red ground under the part that did not survive.
 *
 * The plan channel owns the opacity, and — for a CUT — nothing else. A plan that
 * proposes to REMOVE a sentence is not the author of that sentence; somebody else
 * wrote it, and repainting it in the plan's gray erases exactly the fact the reader
 * needs to weigh the proposal: whose words are on the block. So a cut is struck and
 * faded and keeps whatever ink it already had — the body colour when the prose is
 * settled, the author's blue when the removal lands on words they have not sent yet.
 * "The agent proposes to cut a line YOU just wrote" and "the agent proposes to cut a
 * line the loop wrote last week" are different situations and now look it.
 *
 * ## What may stack, and what may never
 *
 * Composition is the design, so the combinations are the specification. Read a span as
 * its INK (who wrote it) over its GROUND (what the codebase did with it):
 *
 *   ground \ ink │  none            blue (human)          gray (plan)
 *   ─────────────┼──────────────────────────────────────────────────────────────
 *   none         │  settled prose   you wrote it,          planned; nothing is
 *                │                  not built yet          built yet
 *   green (add)  │  the codebase    ✗ IMPOSSIBLE           planned, and the build
 *                │  added this                             put these words in
 *   red (cut)    │  the codebase    ✗ IMPOSSIBLE           planned, and the build
 *                │  dropped this                           did NOT keep it
 *
 * The bottom-right cell is the one the whole model exists for: *this is what we agreed
 * to, and here is where what got built came out different*. Nothing is written to
 * produce it — it falls out of two channels drawing two properties of the same words.
 *
 * The two ✗ cells are contradictions, not merely unusual. "You wrote this" and "the
 * codebase wrote this" cannot both be true of one sentence, and a reader looking at
 * blue-on-green has no way to tell which half is lying. Three rules keep them empty:
 *
 *   1. Human and code claims are computed against the same text (`projected`) and the
 *      code claim yields wherever the human also claims. The author is the one party
 *      who can be asked, so they win — the same rule `model.event.outranks` states.
 *   2. A code claim is ALL OR NOTHING through the author's later typing, so editing
 *      inside a code-surfaced sentence voids it rather than splitting it.
 *   3. Human claims are the INSERTED runs of a diff, and inserted text is by
 *      construction absent from the `same` regions any other channel maps through.
 *
 * Human and plan cannot collide on an ADD, for (3)'s reason in both directions: the
 * plan lives strictly between the two texts the human channel is measured across. On a
 * CUT they collide by design and must — the words a plan proposes to remove are words
 * that already existed, so they may well be the author's own. That is a legible
 * composition rather than a contradiction (blue ink, struck and faded: *the agent wants
 * to drop the line you just wrote*), and it is why the plan channel gives up the ink
 * axis for cuts and keeps only the opacity.
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
 * proposal has still told you something. Every claim here is DERIVED, never stored, so
 * "what happens if they never answer" needs no mechanism of its own: the next payload
 * recomputes from whatever is true then. Three consequences, and they are the whole of
 * the drop policy:
 *
 *   • A proposal the daemon stops offering simply stops producing claims — it is not in
 *     `plan` any more. Superseded, withdrawn and applied all look the same from here,
 *     which is correct: none of them is still a question.
 *   • A REPLACEMENT proposal is computed against the store's current text, because that
 *     is what the daemon proposed against. Its diff is therefore fresh, and no trace of
 *     the unanswered one survives to be mixed into it.
 *   • Unanswered TYPING keeps its marks, because `humanBase` moves only when the feature
 *     adopts a projection (`state/edit-baseline.rebaseCaptured`). This is the one place
 *     the "assume they meant what is on screen" reading has teeth, and it is also the
 *     one place getting it wrong loses work rather than just redrawing.
 *
 * Pure — no DOM, no TipTap, no vscode. `settlement.test.ts` drives it directly.
 */
import { wordDiff, sentenceDiff, type DiffRun } from './doc-diff';
import { alignParas, orphans } from './para-align';

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

/**
 * What the claim did to the text. Three, not two, and the third is not a refinement —
 * it is a different situation:
 *
 *   add  — a range. This channel put these words here.
 *   cut  — a range. The words are STILL HERE and this channel proposes they go.
 *   del  — a point, carrying the words. They are already gone; there is nothing to cover.
 *
 * `cut` exists because a proposal is materialized as old-AND-new (the tracked-change
 * engine keeps the displaced sentence in the document so the reader can compare), so
 * the text a plan wants removed is on screen. Drawing that as a `del` point would print
 * the sentence a second time, beside the copy already there.
 */
export type Edit = 'add' | 'cut' | 'del';

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
    /**
     * On a CODE claim: these words came from the accepted plan, and the build did not
     * keep them. It is the one composition a `del` cannot express by stacking, because
     * a `del` prints its own ghost rather than covering text that is on screen — so
     * there is nothing for a plan claim to stack ON, and the sentence the reader
     * agreed to would reprint as an anonymous red ghost, indistinguishable from a
     * line the codebase dropped that nobody had ever promised.
     *
     * Derived, like everything else here: the words are in `code.prev` (the wording
     * that existed once the plan was applied) and absent from `accepted.prev` (the
     * wording before it), which is exactly what "the plan put them there" means.
     */
    planned?: boolean;
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
    /**
     * The last text the CODE agreed with — what the author's own edits are measured
     * against. Absent ⇒ `planned`, which is right only while nothing is committed.
     *
     * It has to be separate from `projected`, and the case that forces it is the
     * ordinary one: you edit, you press ⌘S, the daemon applies the edit and projects
     * it straight back. `projected` now EQUALS what you typed, so a human diff taken
     * against it is empty — and the blue ink saying "this is yours, the code has not
     * caught up" vanishes at the exact moment it starts being true. The daemon ships
     * the pre-edit text for precisely this (`hold_detail.baseline`); before hand-off
     * the editor's own frozen baseline plays the same role.
     *
     * Note this is the base for the human CLAIMS only. Carrying the plan and code
     * spans forward still walks `planned → live`, because that is the transformation
     * the text actually underwent and positions have to follow the text.
     */
    humanBase?: FeatureText;
    /** The loop rewrote this description on its own authority; `prev` is what it
     *  displaced. The rewrite is ALREADY in `projected`. */
    code?: { layerId: string; prev: FeatureText };
    /** An UNANSWERED proposal's own runs, per block, in `planned` coordinates — both
     *  the displaced wording and the proposed wording are on screen, because the
     *  tracked-change engine materialized the proposal into the document. */
    plan?: { layerId: string; stage: 'proposed'; runs: BlockRuns[] };
    /**
     * A plan the reader ACCEPTED, whose code has not landed yet. `prev` is the wording
     * it replaced.
     *
     * It is a separate field from `plan` because it has a different GEOMETRY, and that
     * is not a detail: accepting APPLIES the proposal, so the plan's words are in
     * `projected` and the words they displaced are gone from the page. A materialized
     * proposal has both sides present; this has one. Reusing `plan.runs` for it would
     * draw the displaced sentence a second time, over text that is not there.
     *
     * Without this the accepted stage was unreachable. Every plan claim came from the
     * pending-proposal list, and accepting DELETES that row — so the plan's wording
     * silently became ordinary prose at the exact moment it started being a promise the
     * codebase had not kept yet, and the one composition the reader most needs after an
     * agent works (planned wording, with the build's own green and red under the parts
     * that came out differently) could never be drawn at all.
     *
     * Source: the queued realize directive's baseline, with `origin: "plan"` saying the
     * queue is holding an agent's accepted plan rather than the author's own typing
     * (`hold_detail`, `codoc/loop/edits.Directive.origin`).
     */
    accepted?: { layerId: string; prev: FeatureText };
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

interface Span0 { edit: Edit; start: number; end: number; removed?: string }

/**
 * Spans of a diff, in NEW coordinates — where the deleted text is NOT present.
 *
 * A deletion becomes a zero-width point carrying its words, because there is nothing
 * left to underline: "I don't think" → "I| think" is the case that taught this.
 *
 * Every channel produces these, INCLUDING the human one, even though the human channel
 * does not draw them (see `settlement-decorations`). The claim still has to exist: a
 * feature the author has only deleted from has no added words to ink, so suppressing it
 * here would leave the model reporting a settled feature and the margin marker saying
 * nothing about an edit that is real and unsent. What is a rendering decision is kept a
 * rendering decision.
 */
function spansOf(runs: readonly DiffRun[]): Span0[] {
    const out: Span0[] = [];
    let at = 0;
    for (const run of runs) {
        if (run.t === 'same') { at += run.s.length; continue; }
        if (run.t === 'ins') { out.push({ edit: 'add', start: at, end: at + run.s.length }); at += run.s.length; continue; }
        out.push({ edit: 'del', start: at, end: at, removed: run.s });
    }
    return out;
}

/**
 * Spans of a diff, in MATERIALIZED coordinates — where BOTH sides are present.
 *
 * This is the plan channel's geometry and it is genuinely different: the tracked-change
 * engine writes the displaced sentence into the document alongside the replacement, so
 * every run occupies real characters and offsets advance through all three kinds. A
 * removal is therefore a `cut` over text that is on screen, not a point standing in for
 * text that is not.
 */
function materializedSpans(runs: readonly DiffRun[]): Span0[] {
    const out: Span0[] = [];
    let at = 0;
    for (const run of runs) {
        const end = at + run.s.length;
        if (run.t === 'ins') out.push({ edit: 'add', start: at, end });
        else if (run.t === 'del') out.push({ edit: 'cut', start: at, end, removed: run.s });
        at = end;
    }
    return out;
}

/**
 * Every live block, carrying the same text as each earlier stage held it.
 *
 * All four stages are resolved into LIVE coordinates up front, by chaining the
 * content-based pairings backwards (live ← planned ← projected ← prev). Everything
 * downstream then works inside one block, and no claim can be computed in one
 * paragraph's coordinates and drawn in another's — the failure a per-stage index
 * walk invites, because a single paragraph inserted at the top of a description
 * shifts every index under it.
 */
interface BlockStages {
    block: BlockRef;
    prev: string;
    projected: string;
    planned: string;
    /** The last wording the code agreed with — see `FeatureLayers.humanBase`. */
    humanBase: string;
    /** What an ACCEPTED plan replaced — see `FeatureLayers.accepted`. Like `humanBase`
     *  it is a sibling of `projected`, not an ancestor: the plan is already applied. */
    acceptedPrev: string;
    live: string;
}

/** A paragraph that existed at an earlier stage and has no live counterpart. Its
 *  removal survives in no block's diff, so it is carried out separately and drawn as
 *  a deletion point on the block that now stands where it stood. */
interface Orphan {
    channel: Channel;
    text: string;
    /** Live block to anchor on; `null` ⇒ the end of the last live block. */
    anchor: BlockRef | null;
    layerId: string;
    stage: Stage;
    /** See `Claim.planned` — a whole paragraph the plan added and the build dropped. */
    planned?: boolean;
}

function paraRef(index: number): BlockRef { return { kind: 'para', index }; }

function blockStages(f: FeatureLayers): BlockStages[] {
    const planned = f.planned ?? f.projected;
    const prev = f.code?.prev ?? f.projected;
    const humanBase = f.humanBase ?? planned;
    const toPlanned = alignParas(planned.paras, f.live.paras);
    const plannedToProjected = alignParas(f.projected.paras, planned.paras);
    const projectedToPrev = alignParas(prev.paras, f.projected.paras);
    // Paired to LIVE directly, not through the chain: the human base is a sibling of
    // `projected`, not an ancestor of it — it is what the code last agreed with, which
    // may be older than anything the daemon has since projected. The accepted plan's
    // displaced wording is a sibling in the same sense, and pairs the same way.
    const toHumanBase = alignParas(humanBase.paras, f.live.paras);
    const acceptedPrev = f.accepted?.prev ?? f.projected;
    const toAccepted = alignParas(acceptedPrev.paras, f.live.paras);

    const out: BlockStages[] = [{
        block: { kind: 'title' },
        prev: prev.title, projected: f.projected.title, planned: planned.title,
        humanBase: humanBase.title, acceptedPrev: acceptedPrev.title, live: f.live.title,
    }];
    for (let i = 0; i < f.live.paras.length; i++) {
        const p = toPlanned[i];
        const j = p === null ? null : plannedToProjected[p] ?? null;
        const k = j === null ? null : projectedToPrev[j] ?? null;
        const h = toHumanBase[i];
        const a = toAccepted[i];
        out.push({
            block: paraRef(i),
            live: f.live.paras[i],
            planned: p === null ? '' : planned.paras[p] ?? '',
            projected: j === null ? '' : f.projected.paras[j] ?? '',
            prev: k === null ? '' : prev.paras[k] ?? '',
            humanBase: h === null ? '' : humanBase.paras[h] ?? '',
            acceptedPrev: a === null ? '' : acceptedPrev.paras[a] ?? '',
        });
    }
    return out;
}

/** Whole paragraphs dropped between two stages, with the live block they now belong to. */
function orphansBetween(
    base: string[], cur: string[], toLive: (curIndex: number) => number | null,
    channel: Channel, layerId: string, stage: Stage,
    fromPlan?: (text: string) => boolean,
): Orphan[] {
    const pairing = alignParas(base, cur);
    return orphans(base, cur, pairing).map(o => {
        const live = o.anchorIndex === null ? null : toLive(o.anchorIndex);
        return {
            channel, text: base[o.baseIndex], layerId, stage,
            planned: fromPlan ? fromPlan(base[o.baseIndex]) : undefined,
            anchor: live === null ? null : paraRef(live),
        };
    });
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
    const out: Claim[] = [];
    const humanStage: Stage = f.committed ? 'committed' : 'open';
    const planRunsFor = new Map<string, DiffRun[]>();
    if (f.plan) for (const b of f.plan.runs) planRunsFor.set(keyOf(b.block), b.runs);

    for (const st of blockStages(f)) {
        const planRuns = diffFor(st.block, 'plan')(st.projected, st.planned);
        const humanRuns = diffFor(st.block, 'human')(st.planned, st.live);
        const toLive = forwardMap(humanRuns);

        // The author's ALREADY-APPLIED edit, in live coordinates — hop (a) of the human
        // channel (see below). Computed here, ahead of the code channel, because the
        // code channel has to know about it: both are diffs INTO `projected`, so they
        // can name the same words, and blue ink over a green ground is a cell of the
        // grammar that must stay empty. See `humanAdds`.
        const humanApplied = f.humanBase && st.humanBase !== st.projected
            ? spansOf(diffFor(st.block, 'human')(st.humanBase, st.projected))
            : [];

        // ── code: computed against `projected`, carried forward into live ────
        if (f.code && st.prev !== st.projected) {
            const point = (off: number): number =>
                toLive(forwardMap(planRuns)(off, BEFORE), BEFORE);
            // Did the accepted plan put these words here? See `Claim.planned`. Only
            // askable when there IS an accepted plan; with none, a removal is just a
            // removal and claiming otherwise would put a promise on every deleted line.
            const fromPlan = (text: string): boolean =>
                !!f.accepted && !!text.trim() && !st.acceptedPrev.includes(text.trim());
            // The author's own added spans, in the SAME (projected) coordinates the code
            // diff runs in, so the overlap test below is a plain interval test.
            const humanAdds = humanApplied.filter(s => s.edit === 'add');
            for (const s of spansOf(diffFor(st.block, 'code')(st.prev, st.projected))) {
                if (s.edit === 'del') {
                    const at = point(s.start);
                    out.push({ channel: 'code', stage: 'landed', edit: 'del', block: st.block, start: at, end: at, removed: s.removed, planned: fromPlan(s.removed ?? ''), layerId: f.code.layerId });
                    continue;
                }
                // A sentence the AUTHOR also claims is not a sentence the code report is
                // about. This is the same all-or-nothing rule as below, applied one hop
                // earlier: `humanBase → projected` and `prev → projected` are two diffs
                // into the same text, so a description the loop rewrote and the author
                // then edited can have both channels naming the same words. Drawn, that
                // is blue ink on a green ground — "you wrote this" and "the codebase
                // wrote this" about one sentence, which cannot both be true and gives the
                // reader no way to tell which half is lying. The author wins: they are
                // the one party who can be asked.
                if (humanAdds.some(h => h.start < s.end && s.start < h.end)) continue;
                // ALL OR NOTHING, and deliberately so. The code channel reports what
                // the codebase says, at the granularity of a sentence. The moment the
                // author edits inside that sentence it is no longer the sentence the
                // report was about, and marking the surviving fragment green would
                // point at words ("The uploader ") that carry none of the claim. The
                // plan channel gets the opposite treatment, for the opposite reason.
                const hops = mapSpan(planRuns, s.start, s.end)
                    .flatMap(x => mapSpan(humanRuns, x.start, x.end));
                if (covered(hops) !== s.end - s.start) continue;
                for (const h of hops) {
                    out.push({ channel: 'code', stage: 'landed', edit: 'add', block: st.block, start: h.start, end: h.end, layerId: f.code.layerId });
                }
            }
        }

        // ── plan/accepted: agreed wording the code has not caught up with ────
        //
        // Same geometry as the code channel — the words are in `projected` and what
        // they displaced is gone — and the OPPOSITE drop rule, which is the plan
        // channel's rule throughout: SPLIT. A fragment of the plan the author has since
        // edited around is still the plan's, and voiding the whole span would drop the
        // gray at the moment it matters most, when the reader is comparing what was
        // agreed against what the build produced.
        if (f.accepted && st.acceptedPrev !== st.projected) {
            const point = (off: number): number =>
                toLive(forwardMap(planRuns)(off, BEFORE), BEFORE);
            for (const s of spansOf(diffFor(st.block, 'plan')(st.acceptedPrev, st.projected))) {
                if (s.edit === 'del') {
                    const at = point(s.start);
                    out.push({ channel: 'plan', stage: 'accepted', edit: 'del', block: st.block, start: at, end: at, removed: s.removed, layerId: f.accepted.layerId });
                    continue;
                }
                for (const h of mapSpan(planRuns, s.start, s.end)
                    .flatMap(x => mapSpan(humanRuns, x.start, x.end))) {
                    if (h.start >= h.end) continue;
                    out.push({ channel: 'plan', stage: 'accepted', edit: 'add', block: st.block, start: h.start, end: h.end, layerId: f.accepted.layerId });
                }
            }
        }

        // ── plan: the materialized proposal's own runs, carried through typing ─
        const runs = planRunsFor.get(keyOf(st.block));
        if (f.plan && runs) {
            for (const s of materializedSpans(runs)) {
                // SPLIT, never voided. A proposal is text you are meant to edit in
                // place before accepting it, so typing inside one is ordinary use —
                // and the mark has to survive that, tightened around the author's
                // words rather than swallowing them or vanishing.
                for (const h of mapSpan(humanRuns, s.start, s.end)) {
                    if (h.start >= h.end) continue;
                    out.push({ channel: 'plan', stage: f.plan.stage, edit: s.edit, block: st.block, start: h.start, end: h.end, removed: s.removed, layerId: f.plan.layerId });
                }
            }
        }

        // ── human: the author's own words, in TWO hops, never one ────────────
        //
        // The single diff `humanBase → live` is the obvious form and it is wrong the
        // moment a plan is on screen: the plan's sentences are in `live` and not in
        // `humanBase`, so they come back as text the author had just typed — the
        // agent's proposal inked blue and offered for ⌘S. The chain the text actually
        // walked has a stage in the middle, and the honest decomposition follows it:
        //
        //   humanBase ──(a) applied edit──▶ projected ──plan──▶ planned ──(b) typing──▶ live
        //
        // (a) is what the author already handed over — the daemon applied it, so it is
        // in `projected` — carried forward into live coordinates. (b) is what is on
        // screen and not in the store yet. Neither can pick up the plan's words,
        // because the plan lives strictly between them.
        //
        // With no hold baseline `humanBase === planned`, (a) is empty and (b) is the
        // whole of it — the ordinary case is unchanged.
        if (humanApplied.length) {
            const point = (off: number): number =>
                toLive(forwardMap(planRuns)(off, BEFORE), BEFORE);
            for (const s of humanApplied) {
                if (s.edit === 'del') {
                    const at = point(s.start);
                    out.push({ channel: 'human', stage: 'committed', edit: 'del', block: st.block, start: at, end: at, removed: s.removed, layerId: LOCAL_EDIT_LAYER });
                    continue;
                }
                // SPLIT, like the plan channel and unlike the code channel: these are
                // the author's OWN words, so a fragment of them that survived a later
                // edit is still theirs. Voiding the span the way a code claim is voided
                // would drop the author's ink the moment a proposal touched the same
                // sentence — the one span they most need to see is still outstanding.
                for (const h of mapSpan(planRuns, s.start, s.end)
                    .flatMap(x => mapSpan(humanRuns, x.start, x.end))) {
                    if (h.start >= h.end) continue;
                    out.push({ channel: 'human', stage: 'committed', edit: 'add', block: st.block, start: h.start, end: h.end, layerId: LOCAL_EDIT_LAYER });
                }
            }
        }
        if (st.planned !== st.live) {
            for (const s of spansOf(humanRuns)) {
                out.push({ channel: 'human', stage: humanStage, edit: s.edit, block: st.block, start: s.start, end: s.end, removed: s.removed, layerId: LOCAL_EDIT_LAYER });
            }
        }
    }

    // ── paragraphs that vanished entirely, which no block's diff can report ──
    const planned = f.planned ?? f.projected;
    // Where a trailing removal hangs when it has no following block to anchor to: the
    // last live paragraph, or — when the node has NO live paragraphs left — the heading
    // itself. That last case is a whole node being removed, and without the fallback its
    // entire description would be dropped silently: there is no surviving block to carry
    // it, so the one change nobody could miss on the page became the one the model did
    // not report.
    const lastLive: BlockRef = f.live.paras.length
        ? paraRef(f.live.paras.length - 1) : { kind: 'title' };
    const lastLen = lastLive.kind === 'para'
        ? (f.live.paras[lastLive.index] ?? '').length : f.live.title.length;
    // The same two hops the in-block human claims walk (see above): a paragraph the
    // author's applied edit removed is measured against `projected` and anchored
    // through the plan, and one their unsent typing removed is measured against
    // `planned`. Measuring both against `live` in one step would report every
    // paragraph a plan proposes to ADD as one the author had deleted.
    const dropped: Orphan[] = [
        ...(f.humanBase
            ? orphansBetween(f.humanBase.paras, f.projected.paras, projectedToLive(f), 'human', LOCAL_EDIT_LAYER, 'committed')
            : []),
        ...orphansBetween(planned.paras, f.live.paras, i => i, 'human', LOCAL_EDIT_LAYER, humanStage),
        ...(f.code ? orphansBetween(f.code.prev.paras, f.projected.paras, projectedToLive(f), 'code', f.code.layerId, 'landed',
            // A whole paragraph the plan added and the build then dropped — the same
            // question the in-block deletions ask, asked of the paragraph list.
            text => !!f.accepted && !!text.trim()
                && !(f.accepted.prev.paras.some(p => p.includes(text.trim())))) : []),
    ];
    for (const o of dropped) {
        const block = o.anchor ?? lastLive;
        const at = o.anchor ? 0 : lastLen;
        out.push({ channel: o.channel, stage: o.stage, edit: 'del', block, start: at, end: at, removed: o.text, planned: o.planned, layerId: o.layerId });
    }

    // Stacking order — background, then opacity, then ink — so a consumer that draws
    // them in the order given gets code under plan under human without sorting.
    const rank: Record<Channel, number> = { code: 0, plan: 1, human: 2 };
    return out.sort((a, b) => rank[a.channel] - rank[b.channel]);
}

/** Where a projected paragraph index ends up in the live document, via the same
 *  content pairings `blockStages` walks — so an orphan is anchored to the block that
 *  now stands where it stood, not to whatever shares its old number. */
function projectedToLive(f: FeatureLayers): (projectedIndex: number) => number | null {
    const planned = f.planned ?? f.projected;
    const toPlanned = alignParas(planned.paras, f.live.paras);
    const plannedToProjected = alignParas(f.projected.paras, planned.paras);
    return (j: number): number | null => {
        for (let i = 0; i < f.live.paras.length; i++) {
            const p = toPlanned[i];
            if (p !== null && plannedToProjected[p] === j) return i;
        }
        return null;
    };
}

function keyOf(b: BlockRef): string {
    return b.kind === 'title' ? 'title' : 'p' + b.index;
}

// ── superseding: the drop rule nobody has to click ───────────────────────────
