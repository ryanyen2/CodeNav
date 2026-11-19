/**
 * claims.ts: what to draw, from what the projection says.
 *
 * The paints on the prose are the daemon's claims (`derive.feature_claims`):
 * the person's ink, an open plan's words, cuts and mark toggles, and what the
 * code added or removed. Only the viewer's own ink is drawn: another person's
 * words are not the viewer's to hand off. History adds one paint per author,
 * read from the runs. One palette on every surface: blue is the person, gray
 * a plan, green and red the code. Pure and DOM-free; the editor turns
 * `Paint`s into decorations at scalar → position. A block's paints come from
 * its edited field: `source` for a table, a diagram or a code block, `text`
 * otherwise.
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
    text?: string;      // removed text, for the widget; the words a mark toggle covers
}

const HUMAN = 'human:';

/** The paint for one claim. */
export function paintOf(c: Claim, plans: Projection['plans']): Paint {
    const at = { bid: c.bid, start: c.start, end: c.end };
    if (c.channel === 'human') {
        return c.stage === 'sent' ? { ...at, cls: 'ink-sent', title: 'yours, handed off' }
            : { ...at, cls: 'ink-unsent', title: 'yours, not yet handed off' };
    }
    if (c.channel === 'code') {
        return c.stage === 'removed' ? { ...at, cls: 'code-removed', title: 'the code no longer says this', text: c.text }
            : { ...at, cls: 'code-added', title: 'the code now says this' };
    }
    if (c.stage === 'open') {
        const note = plans.find(p => p.id === c.plan)?.note;
        return { ...at, cls: 'plan-open', title: `${c.actor} proposed this${note ? `: ${note}` : ''}` };
    }
    if (c.stage === 'removed') return { ...at, cls: 'plan-cut', title: `${c.actor} proposes removing this` };
    // A mark toggle's text is `<mark>:<words>`.
    const [kind, ...rest] = c.text.split(':');
    const marked = c.stage === 'marked';
    return { ...at, cls: marked ? 'plan-mark' : 'plan-unmark', text: rest.join(':'),
             title: `${c.actor} proposes ${marked ? 'marking' : 'unmarking'} this as ${kind}` };
}

const indexes = new WeakMap<Projection, Map<string, Claim[]>>();

/** The projection's claims on one block, whichever feature published them.
 *  Indexed once per projection. A block a gesture moved to another feature
 *  keeps its paints until the projection that answers the gesture arrives. */
function claimsOn(proj: Projection, bid: string): Claim[] {
    let index = indexes.get(proj);
    if (!index) {
        index = new Map();
        for (const cs of Object.values(proj.claims)) {
            for (const c of cs) {
                const list = index.get(c.bid);
                if (list) list.push(c); else index.set(c.bid, [c]);
            }
        }
        indexes.set(proj, index);
    }
    return index.get(bid) ?? [];
}

/** All paints for one block, from its claims. Ink wins over ground where both
 *  claim a range (the person's words are theirs, whatever the code did around
 *  them). */
export function paintsFor(b: ProjectedBlock, proj: Projection, history = false, viewer = ''): Paint[] {
    // A card (a kind without a node type) holds its words in text areas, which take no paints.
    const f = isBuiltin(b.kind) ? textField(b, pathOf(b.kind)) : null;
    if (!f) return [];
    const mine = claimsOn(proj, b.id)
        .filter(c => c.channel !== 'human' || c.actor === viewer)
        .map(c => paintOf(c, proj.plans));
    const ink = mine.filter(p => p.cls.startsWith('ink-'));
    const ground = mine.filter(p => p.cls.startsWith('code-') && !ink.some(i => i.start < p.end && p.start < i.end));
    return [...(history ? authorPaints(b, f) : []), ...ground, ...mine.filter(p => p.cls.startsWith('plan-')), ...ink];
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

/** For an `edited` feature the viewer has no edits in: who edited it, said so the
 *  person knows their hand-off does not send it. '' for any other feature. */
export function theirEdits(proj: Projection, fid: string, viewer: string): string {
    const who = proj.editors?.[fid] ?? [];
    return proj.phases[fid] === 'edited' && !who.includes(viewer)
        ? `edited by ${who.join(', ')}, not yet handed off; your hand-off does not send it` : '';
}

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
