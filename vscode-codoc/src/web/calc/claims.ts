/**
 * claims.ts: what to draw, from what the projection says.
 *
 * Every visual claim on the prose is read off the projection: a run's actor
 * and whether its op is inside the realized frontier give the human channel
 * (ink); the daemon's `claims` give the code channel (ground) and a plan's mark
 * toggles; `plan` runs and `suggest_del` spans give the rest of the plan
 * channel (opacity). One palette on every
 * surface: blue is the person, gray a plan, green and red the code. Pure and
 * DOM-free; the editor turns `Paint`s into decorations at scalar → position.
 * A block's paints come from its edited field: `source` for a table, a
 * diagram or a code block, `text` otherwise.
 */
import { isBuiltin, pathOf, textField, type Claim, type Phase, type ProjectedBlock, type Projection, type TextField } from './projection';

export type Ink = 'ink-unsent' | 'ink-sent' | 'plan-open' | 'plan-cut' | 'plan-mark' | 'plan-unmark'
    | 'code-added' | 'code-removed' | 'author-human' | 'author-loop' | 'author-agent' | 'author-other';

export interface Paint {
    bid: string;
    start: number;      // scalar offsets, half-open; a removed span is zero-width
    end: number;
    cls: Ink;
    title: string;      // hover text and aria-description
    text?: string;      // removed text, for the widget
}

const HUMAN = 'human:';

/** Whether a frontier already contains an op: `session:seq` with `seq` at or
 *  below what the frontier records for that session. */
export function inFrontier(op: string, frontier: Record<string, number>): boolean {
    const i = op.lastIndexOf(':');
    return i > 0 && Number(op.slice(i + 1)) <= (frontier[op.slice(0, i)] ?? -1);
}

/** The viewer's own words that the code does not yet say: pulsing until they
 *  are handed off, steady once they are. The two watermarks are the whole
 *  distinction. There is no separate "not yet acked" case, because a run
 *  reaches the projection only after its op has been applied. Another
 *  person's words are not the viewer's to hand off, so they are not inked. */
export function inkPaints(b: ProjectedBlock, f: TextField, realized: Record<string, number>,
                          handoff: Record<string, number>, viewer: string): Paint[] {
    const out: Paint[] = [];
    let cur: Paint | null = null;
    for (const r of f.runs) {
        if (r.actor !== viewer || r.plan) { cur = null; continue; }
        if (inFrontier(r.op, realized)) { cur = null; continue; }
        const cls: Ink = inFrontier(r.op, handoff) ? 'ink-sent' : 'ink-unsent';
        if (cur && cur.cls === cls && cur.end === r.start) { cur.end = r.end; continue; }
        cur = { bid: b.id, start: r.start, end: r.end, cls, title: cls === 'ink-sent' ? 'yours, handed off' : 'yours, not yet handed off' };
        out.push(cur);
    }
    return out;
}

/** An open plan's words (gray) and the words it proposes to cut (struck).
 *  The projection lists only open plans: a decided plan's words are ordinary
 *  text, and History still says who proposed them. */
export function planPaints(b: ProjectedBlock, f: TextField, plans: Projection['plans']): Paint[] {
    const status = new Map(plans.map(p => [p.id, p]));
    const out: Paint[] = [];
    for (const r of f.runs) {
        const p = r.plan ? status.get(r.plan) : undefined;
        if (!p) continue;
        out.push({ bid: b.id, start: r.start, end: r.end, cls: 'plan-open',
                   title: `${r.actor} proposed this${p.note ? `: ${p.note}` : ''}` });
    }
    for (const s of f.suggest_del) {
        const p = status.get(s.plan);
        out.push({ bid: b.id, start: s.start, end: s.end, cls: 'plan-cut', title: `${p?.actor ?? 'a plan'} proposes removing this` });
    }
    return out;
}

/** A plan's proposed mark changes on words that are already there: the
 *  document shows what it says today (the rejected read mode), and these say
 *  what the verdict would do to it. */
export function markPaints(claims: Claim[] | undefined): Paint[] {
    if (!claims) return [];
    return claims.filter(c => c.channel === 'plan' && (c.stage === 'marked' || c.stage === 'unmarked')).map(c => {
        const [kind, ...rest] = c.text.split(':');
        return {
            bid: c.bid, start: c.start, end: c.end,
            cls: (c.stage === 'marked' ? 'plan-mark' : 'plan-unmark') as Ink,
            title: `${c.actor} proposes ${c.stage === 'marked' ? 'marking' : 'unmarking'} this as ${kind}`,
            text: rest.join(':'),
        };
    });
}

/** What the code loop added (green) or removed (red) since the viewer's cursor. */
function groundPaints(claims: Claim[] | undefined): Paint[] {
    if (!claims) return [];
    return claims.filter(c => c.channel === 'code').map(c => ({
        bid: c.bid, start: c.start, end: c.end,
        cls: c.stage === 'removed' ? 'code-removed' as Ink : 'code-added' as Ink,
        title: c.stage === 'removed' ? 'the code no longer says this' : 'the code now says this',
        text: c.stage === 'removed' ? c.text : undefined,
    }));
}

/** All paints for one block. Ink wins over ground where both claim a range
 *  (the person's words are theirs, whatever the code did around them). */
export function paintsFor(b: ProjectedBlock, proj: Projection, fid: string | null,
                          watermarks: { handoff: Record<string, number>; realized: Record<string, number> },
                          history = false, viewer = ''): Paint[] {
    // A card (a kind without a node type) holds its words in text areas, which take no paints.
    const f = isBuiltin(b.kind) ? textField(b, pathOf(b.kind)) : null;
    if (!f) return [];
    const ink = inkPaints(b, f, watermarks.realized, watermarks.handoff, viewer);
    const plan = planPaints(b, f, proj.plans);
    const mine = fid ? proj.claims[fid]?.filter(c => c.bid === b.id) : undefined;
    const ground = groundPaints(mine).filter(g => !ink.some(i => i.start < g.end && g.start < i.end));
    return [...(history ? authorPaints(b, f) : []), ...ground, ...plan, ...markPaints(mine), ...ink];
}

/** The margin marker and row glyph for a feature's phase: one palette. */
export const PHASE_LABEL: Record<Phase, string> = {
    synced: 'in step with the code',
    edited: 'edited, not yet handed off',
    queued: 'handed off, being built',
    realizing: 'being built now',
    proposed: 'a plan awaits your verdict',
    drifted: 'the code moved; the prose has not',
    planned: 'planned, no code yet',
};

export const PHASE_CHANNEL: Record<Phase, 'human' | 'plan' | 'code' | 'none'> = {
    synced: 'none', edited: 'human', queued: 'human', realizing: 'none', proposed: 'plan', drifted: 'code', planned: 'plan',
};

/** History: who wrote each run of words, one class per actor kind. */
export function authorPaints(b: ProjectedBlock, f: TextField): Paint[] {
    return displayRuns(f).map(r => {
        const kind = r.actor.startsWith(HUMAN) ? 'human' : r.actor === 'loop' ? 'loop' : r.actor.startsWith('agent:') ? 'agent' : 'other';
        return { bid: b.id, start: r.start, end: r.end, cls: `author-${kind}` as Ink, title: `written by ${r.actor}` };
    });
}

/** Display runs for History: adjacent runs by the same actor merge, then snap
 *  to word boundaries so a one-character fix does not split a word visually. */
export function displayRuns(f: TextField): Array<{ start: number; end: number; actor: string }> {
    const chars = Array.from(f.text);
    const merged: Array<{ start: number; end: number; actor: string }> = [];
    for (const r of f.runs) {
        const last = merged[merged.length - 1];
        if (last && last.actor === r.actor && last.end === r.start) last.end = r.end;
        else merged.push({ start: r.start, end: r.end, actor: r.actor });
    }
    const isWord = (i: number) => i >= 0 && i < chars.length && /[\p{L}\p{N}]/u.test(chars[i]);
    for (let i = 0; i + 1 < merged.length; i++) {
        const a = merged[i], b = merged[i + 1];
        // A boundary inside a word moves to the word's edge; the word goes to the
        // side that holds more of it, the earlier side on a tie.
        if (isWord(a.end - 1) && isWord(b.start)) {
            let ws = b.start, we = b.start;
            while (isWord(ws - 1)) ws--;
            while (isWord(we)) we++;
            const cut = (b.start - ws) >= (we - b.start) ? we : ws;
            a.end = cut;
            b.start = cut;
        }
    }
    return merged.filter(r => r.end > r.start);
}
