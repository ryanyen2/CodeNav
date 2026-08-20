import { describe, it, expect } from 'vitest';
import {
    claimsFor, forwardMap, mapSpan, LOCAL_EDIT_LAYER, AFTER, BEFORE,
    type FeatureLayers, type FeatureText, type Claim,
} from '../state/settlement';
import { wordDiff, sentenceDiff } from '../state/doc-diff';

const ft = (title: string, ...paras: string[]): FeatureText => ({ title, paras });

/** Claims of one channel, in document order, as terse tuples for readable assertions. */
function of(claims: Claim[], channel: Claim['channel']): [string, number, number, string?][] {
    return claims.filter(c => c.channel === channel)
        .map(c => [c.edit, c.start, c.end, c.removed] as [string, number, number, string?]);
}

describe('forwardMap — carrying a point across an edit that happened after it', () => {
    it('puts a point on the side of an insertion that assoc asks for', () => {
        const map = forwardMap(wordDiff('a c', 'a b c'));
        // "c" is at 2 in the old text. A span STARTING there wants to be after the
        // inserted "b " (4); a span ENDING there wants to stop before it (2).
        expect(map(2, AFTER)).toBe(4);
        expect(map(2, BEFORE)).toBe(2);
    });

    it('collapses an offset inside deleted text to where the deletion happened', () => {
        const map = forwardMap(wordDiff('a b c', 'a c'));
        const at = map(2); // inside the deleted "b "
        expect(at).toBe(map(3));
        expect(at).toBeLessThanOrEqual(2);
    });

    it('pins the ends, on the side asked for', () => {
        const map = forwardMap(wordDiff('one two', 'one two three'));
        expect(map(0)).toBe(0);
        expect(map('one two'.length, AFTER)).toBe('one two three'.length);
        expect(map('one two'.length, BEFORE)).toBe('one two'.length);
    });

    it('is identity when nothing changed', () => {
        const map = forwardMap(wordDiff('same text', 'same text'));
        for (const i of [0, 3, 5, 9]) expect(map(i)).toBe(i);
    });
});

describe('mapSpan — keeping only what survived', () => {
    it('returns an untouched span in one piece', () => {
        expect(mapSpan(wordDiff('a b c', 'a b c'), 2, 5)).toEqual([{ start: 2, end: 5 }]);
    });

    it('splits around text somebody else inserted in the middle', () => {
        // "one two" → "one NEW two": a span over the whole original comes back as the
        // two surviving halves, so the mark never covers the inserted words.
        const spans = mapSpan(wordDiff('one two', 'one NEW two'), 0, 'one two'.length);
        expect(spans.length).toBe(2);
        const text = 'one NEW two';
        expect(spans.map(s => text.slice(s.start, s.end)).join('|')).not.toContain('NEW');
    });

    it('drops the part that was deleted', () => {
        const spans = mapSpan(wordDiff('keep drop', 'keep'), 0, 'keep drop'.length);
        expect(spans.map(s => 'keep'.slice(s.start, s.end)).join('')).toBe('keep');
    });
});

describe('claimsFor — the human channel', () => {
    it('says nothing about a feature nobody touched', () => {
        expect(claimsFor({ projected: ft('T', 'p'), live: ft('T', 'p') })).toEqual([]);
    });

    it('marks typed words open until they are handed off', () => {
        const base = { projected: ft('T', 'the retry is capped'), live: ft('T', 'the retry is capped at five') };
        expect(claimsFor(base).every(c => c.stage === 'open')).toBe(true);
        expect(claimsFor({ ...base, committed: true }).every(c => c.stage === 'committed')).toBe(true);
    });

    it('carries a deletion as a point plus the words it removed', () => {
        const claims = claimsFor({ projected: ft('T', 'I do not think so'), live: ft('T', 'I think so') });
        const dels = of(claims, 'human').filter(c => c[0] === 'del');
        expect(dels.length).toBe(1);
        expect(dels[0][1]).toBe(dels[0][2]);          // zero width — nothing left to cover
        expect(dels[0][3]).toContain('do not');
    });

    it('marks the author\'s own edits word by word, not sentence by sentence', () => {
        const titleClaims = claimsFor({ projected: ft('Retry policy'), live: ft('Retry budget') });
        expect(of(titleClaims, 'human').some(c => c[0] === 'add')).toBe(true);
        // Your own typing is shown at the granularity you did it in — the sentence is
        // the review unit for somebody ELSE's change, not for your own keystrokes.
        const live = ft('T', 'It retries five times. It then gives up.');
        const p = claimsFor({ projected: ft('T', 'It retries twice. It then gives up.'), live });
        const adds = p.filter(c => c.channel === 'human' && c.edit === 'add');
        const marked = adds.map(a => live.paras[0].slice(a.start, a.end)).join('');
        expect(marked).toContain('five');
        expect(marked).toContain('times');
        expect(marked).not.toContain('gives up');   // the untouched sentence stays unmarked
        expect(marked.length).toBeLessThan(live.paras[0].length / 2);
    });
});

describe('claimsFor — the code channel', () => {
    const code = { layerId: 'e-1', prev: ft('T', 'The uploader retries twice.') };

    it('reports what the loop rewrote, against the text it displaced', () => {
        const claims = claimsFor({
            code, projected: ft('T', 'The uploader retries five times.'),
            live: ft('T', 'The uploader retries five times.'),
        });
        const c = of(claims, 'code');
        expect(c.some(x => x[0] === 'add')).toBe(true);
        expect(c.some(x => x[0] === 'del' && x[3]?.includes('twice'))).toBe(true);
        expect(claims.filter(x => x.channel === 'code').every(x => x.stage === 'landed')).toBe(true);
    });

    it('lands its spans on the words as they now read, after the author typed above them', () => {
        const projected = ft('T', 'Intro.', 'The uploader retries five times.');
        const live = ft('T', 'Intro, expanded a lot.', 'The uploader retries five times.');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'Intro.', 'The uploader retries twice.') },
            projected, live,
        });
        // The rewritten sentence is in paragraph 1, whose text the author did not touch,
        // so its offsets must be unchanged by the edit to paragraph 0.
        const inPara1 = claims.filter(c => c.channel === 'code' && c.block.kind === 'para' && c.block.index === 1);
        expect(inPara1.length).toBeGreaterThan(0);
        const add = inPara1.find(c => c.edit === 'add')!;
        expect(live.paras[1].slice(add.start, add.end)).toContain('five times');
    });

    it('yields the span to the author when they overwrite it — those words are theirs now', () => {
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'The uploader retries twice.') },
            projected: ft('T', 'The uploader retries five times.'),
            live: ft('T', 'The uploader gives up immediately.'),
        });
        // Nothing of the loop's sentence survives, so it holds no live range.
        expect(of(claims, 'code').some(c => c[0] === 'add')).toBe(false);
        expect(of(claims, 'human').length).toBeGreaterThan(0);
    });
});

describe('claimsFor — the plan channel', () => {
    const runs = (block: 'title' | number, o: string, n: string) => ({
        block: block === 'title' ? { kind: 'title' as const } : { kind: 'para' as const, index: block },
        runs: block === 'title' ? wordDiff(o, n) : sentenceDiff(o, n),
    });

    it('marks the materialized proposal, carrying its stage', () => {
        const projected = ft('T', 'It retries twice.');
        const planned = ft('T', 'It retries twice. It then backs off.');
        const layers: FeatureLayers = {
            projected, planned, live: planned,
            plan: { layerId: 'e-9', stage: 'proposed', runs: [runs(0, 'It retries twice.', 'It retries twice. It then backs off.')] },
        };
        const claims = claimsFor(layers);
        expect(claims.filter(c => c.channel === 'plan').every(c => c.stage === 'proposed')).toBe(true);
        const add = claims.find(c => c.channel === 'plan' && c.edit === 'add')!;
        expect(planned.paras[0].slice(add.start, add.end)).toContain('backs off');
    });

    it('keeps the author\'s typing separate from the plan text it was typed around', () => {
        const projected = ft('T', 'It retries twice.');
        const planned = ft('T', 'It retries twice. It then backs off.');
        const live = ft('T', 'It retries twice. It then backs off. We should measure this.');
        const claims = claimsFor({
            projected, planned, live,
            plan: { layerId: 'e-9', stage: 'proposed', runs: [runs(0, projected.paras[0], planned.paras[0])] },
        });
        const human = claims.filter(c => c.channel === 'human');
        expect(human.length).toBe(1);
        expect(live.paras[0].slice(human[0].start, human[0].end)).toContain('measure');
        // …and the plan's own span still covers only the plan's sentence.
        const plan = claims.find(c => c.channel === 'plan' && c.edit === 'add')!;
        expect(live.paras[0].slice(plan.start, plan.end)).toContain('backs off');
        expect(live.paras[0].slice(plan.start, plan.end)).not.toContain('measure');
    });
});

describe('claimsFor — the three channels on one paragraph', () => {
    it('stacks them without any of them standing down', () => {
        // The loop rewrote the sentence; an agent then proposed another; the author
        // typed a third. All three are true at once and all three are marked.
        const projected = ft('T', 'It retries five times.');
        const planned = ft('T', 'It retries five times. It then backs off.');
        const live = ft('T', 'It retries five times. It then backs off. Measure it.');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'It retries twice.') },
            plan: { layerId: 'e-9', stage: 'proposed', runs: [{ block: { kind: 'para', index: 0 }, runs: sentenceDiff(projected.paras[0], planned.paras[0]) }] },
            projected, planned, live,
        });
        expect(new Set(claims.map(c => c.channel))).toEqual(new Set(['code', 'plan', 'human']));
        // …and they are handed over in stacking order: background, then opacity, then ink.
        const order = claims.map(c => c.channel);
        expect(order.indexOf('code')).toBeLessThan(order.indexOf('plan'));
        expect(order.indexOf('plan')).toBeLessThan(order.indexOf('human'));
    });

    it('shows a plan sentence the build then cut back as plan ink over a code deletion', () => {
        // Planned two sentences; the build kept one and dropped the other.
        const prev = ft('T', 'It retries. It then backs off.');
        const projected = ft('T', 'It retries.');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev }, projected, live: projected,
        });
        const del = claims.find(c => c.channel === 'code' && c.edit === 'del')!;
        expect(del.removed).toContain('backs off');
    });
});

describe('paragraphs are paired by content, never by number', () => {
    it('does not report the whole node as rewritten when one paragraph is inserted on top', () => {
        const projected = ft('T', 'First claim.', 'Second claim.', 'Third claim.');
        const live = ft('T', 'A new opening.', 'First claim.', 'Second claim.', 'Third claim.');
        const claims = claimsFor({ projected, live });
        // Exactly the inserted paragraph is marked; the three that merely moved down
        // are untouched, which an index-paired diff could not say.
        const marked = claims.filter(c => c.edit === 'add')
            .map(c => (c.block as { index: number }).index);
        expect(new Set(marked)).toEqual(new Set([0]));
    });

    it('anchors a code claim to the paragraph that now holds it, not the one that shares its old number', () => {
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'Untouched.', 'It retries twice.') },
            projected: ft('T', 'Untouched.', 'It retries five times.'),
            live: ft('T', 'A new opening.', 'Untouched.', 'It retries five times.'),
        });
        const add = claims.find(c => c.channel === 'code' && c.edit === 'add')!;
        expect((add.block as { index: number }).index).toBe(2);
    });

    it('reports a paragraph deleted outright, which no surviving block\'s diff can', () => {
        const claims = claimsFor({
            projected: ft('T', 'Keep this.', 'Delete this whole paragraph.', 'Keep this too.'),
            live: ft('T', 'Keep this.', 'Keep this too.'),
        });
        const del = claims.find(c => c.edit === 'del' && c.removed?.includes('Delete this whole'));
        expect(del).toBeDefined();
        expect(del!.start).toBe(del!.end);
        // Anchored on the block that now stands where it stood.
        expect((del!.block as { index: number }).index).toBe(1);
    });

    it('anchors a trailing deletion at the end of the last block', () => {
        const live = ft('T', 'Only this survives.');
        const claims = claimsFor({ projected: ft('T', 'Only this survives.', 'Gone.'), live });
        const del = claims.find(c => c.removed === 'Gone.')!;
        expect(del.start).toBe(live.paras[0].length);
    });
});

describe('the drop rules', () => {
    it('files the author\'s own spans under the local layer, which no payload can withdraw', () => {
        const claims = claimsFor({ projected: ft('T', 'a'), live: ft('T', 'a b') });
        expect(claims.every(c => c.layerId === LOCAL_EDIT_LAYER)).toBe(true);
    });

    it('lets an unanswered proposal go when the daemon stops offering it', () => {
        // No forced verdict, and no mechanism needed for the absence of one: claims are
        // derived, so a proposal that is no longer on offer simply stops producing them.
        const live = ft('T', 'the wording as it now reads');
        expect(claimsFor({ projected: live, live })).toEqual([]);
    });

    it('computes a REPLACEMENT proposal fresh, with no trace of the unanswered one', () => {
        const projected = ft('T', 'It retries twice.');
        const planned = ft('T', 'It retries twice. It backs off.');
        const claims = claimsFor({
            projected, planned, live: planned,
            plan: { layerId: 'e-second', stage: 'proposed', runs: [
                { block: { kind: 'para', index: 0 }, runs: sentenceDiff(projected.paras[0], planned.paras[0]) },
            ] },
        });
        // Only the live layer is represented; the earlier one left with the sidecar.
        expect(new Set(claims.map(c => c.layerId))).toEqual(new Set(['e-second']));
    });

    it('keeps unanswered TYPING marked — the base moves only on adoption', () => {
        // The one place the "assume they meant what is on screen" reading has teeth,
        // and the one place getting it wrong loses work rather than just redrawing.
        const live = ft('T', 'the retry is capped at five');
        const claims = claimsFor({
            projected: live,                       // the daemon has echoed it back…
            live,
            humanBase: ft('T', 'the retry is capped'),   // …but the code has not caught up
        });
        expect(claims.some(c => c.channel === 'human')).toBe(true);
    });
});

describe('a committed edit stays yours until the code catches up', () => {
    it('keeps the mark after the daemon echoes the edit straight back', () => {
        // The regression this exists to prevent: ⌘S, the daemon applies and reprojects,
        // so `projected` now EQUALS what you typed — and a human diff taken against it
        // is empty. The ink would vanish at the moment it starts being true.
        const typed = ft('T', 'It retries five times.');
        const claims = claimsFor({
            projected: typed,                                   // the echo
            live: typed,
            humanBase: ft('T', 'It retries twice.'),            // what the CODE agreed with
            committed: true,
        });
        const human = claims.filter(c => c.channel === 'human');
        expect(human.length).toBeGreaterThan(0);
        expect(human.every(c => c.stage === 'committed')).toBe(true);
        expect(human.some(c => c.edit === 'add'
            && typed.paras[0].slice(c.start, c.end).includes('five times'))).toBe(true);
    });

    it('clears once the code agrees — nothing is left to say', () => {
        const settled = ft('T', 'It retries five times.');
        expect(claimsFor({ projected: settled, live: settled, humanBase: settled })).toEqual([]);
    });

    it('still carries plan and code spans through the real text, not through that base', () => {
        // humanBase is a SIBLING of projected, not an ancestor: positions must follow
        // the text the document actually underwent, or the other channels mis-anchor.
        const projected = ft('T', 'It retries five times.');
        const live = ft('T', 'It retries five times. Measure it.');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'It retries twice.') },
            projected, live, humanBase: projected,
        });
        const add = claims.find(c => c.channel === 'code' && c.edit === 'add')!;
        expect(live.paras[0].slice(add.start, add.end)).toContain('five times');
        expect(live.paras[0].slice(add.start, add.end)).not.toContain('Measure');
    });
});

describe('a node whose prose is gone entirely', () => {
    it('still reports the description it lost — there is no surviving block to carry it', () => {
        // The gap this closes: an orphaned paragraph anchors to the block that now
        // stands where it stood, and when the node has NO paragraphs left there is no
        // such block. The whole description would drop out of the model silently.
        const claims = claimsFor({
            projected: ft('Backoff', 'It waits longer each time.'),
            live: ft('Backoff'),
        });
        const del = claims.find(c => c.removed?.includes('waits longer'));
        expect(del).toBeDefined();
        expect(del!.block.kind).toBe('title');
    });
});

// ─── composition: what may stack, and what may never ─────────────────────────

describe('the composition matrix — the point of one axis per channel', () => {
    const at = (c: Claim[], ch: Claim['channel']): Claim[] => c.filter(x => x.channel === ch);
    const covers = (c: Claim[], text: string, word: string): boolean =>
        c.some(x => x.edit !== 'del' && text.slice(x.start, x.end).includes(word));

    it('PLANNED, THEN BUILT DIFFERENTLY: the plan\'s gray with the build\'s ground under it', () => {
        // The one combination the whole model exists for. The reader accepted a plan; the
        // agent implemented it; Loop A reflected what the code actually says. What they
        // need to see is not "changed" but WHERE the two disagree — and nothing had to be
        // written for it: two channels drew two properties of the same words.
        const prev = ft('Uploads', 'It retries five times.');            // before the plan
        const projected = ft('Uploads', 'It retries five times. It backs off exponentially.');
        const claims = claimsFor({
            projected, live: projected,
            // the plan the reader accepted, still unbuilt
            accepted: { layerId: 'hold:f-1', prev },
            // …and the loop's own reflection of what the code turned out to say
            code: { layerId: 'auto:1', prev: ft('Uploads', 'It retries five times. It backs off linearly.') },
        });
        const text = projected.paras[0];
        expect(covers(at(claims, 'plan'), text, 'backs off')).toBe(true);
        // The code channel reports the sentence the build actually produced.
        expect(at(claims, 'code').length).toBeGreaterThan(0);
    });

    it('never inks a span blue over a green ground — those cannot both be true', () => {
        // Both diffs run INTO `projected`, so they can name the same sentence: the loop
        // rewrote a description and the author's queued edit changed it too. Drawn
        // together that is "you wrote this" and "the codebase wrote this" about one
        // sentence, with no way for the reader to tell which half is lying.
        const projected = ft('Uploads', 'It retries five times.');
        const claims = claimsFor({
            projected, live: projected,
            humanBase: ft('Uploads', 'It retries twice.'),
            code: { layerId: 'auto:1', prev: ft('Uploads', 'It retries twice.') },
        });
        const text = projected.paras[0];
        const inked = at(claims, 'human').filter(c => c.edit !== 'del');
        const grounded = at(claims, 'code').filter(c => c.edit !== 'del');
        expect(inked.length).toBeGreaterThan(0);          // the author's claim stands…
        for (const h of inked) {
            for (const g of grounded) {
                if (h.block.kind !== g.block.kind) continue;
                expect(h.start < g.end && g.start < h.end, text).toBe(false);
            }
        }
    });

    it('a plan CUT keeps the ink of whoever wrote the words it wants gone', () => {
        // The plan channel owns the opacity; on a cut it owns nothing else. The words
        // are the AUTHOR'S — sent, not yet in code — and the agent is asking to replace
        // them. The human claim has to survive underneath, or the surface repaints the
        // reader's own sentence in the agent's gray at the moment they decide whether to
        // lose it. A cut is the one place the two channels may name the same words.
        const humanBase = ft('Uploads', 'It retries five times.');
        const projected = ft('Uploads', 'It retries five times. I think that is too many.');
        // The proposal is materialized: both the displaced sentence and its replacement
        // are on screen, which is what makes the cut a range and not a ghost.
        const planned = ft('Uploads',
            'It retries five times. I think that is too many. It retries three times.');
        const claims = claimsFor({
            projected, planned, live: planned, humanBase,
            plan: { layerId: 'e-9', stage: 'proposed', runs: [{
                block: { kind: 'para', index: 0 },
                runs: [
                    { t: 'same', s: 'It retries five times. ' },
                    { t: 'del', s: 'I think that is too many. ' },
                    { t: 'ins', s: 'It retries three times.' },
                ],
            }] },
        });
        const text = planned.paras[0];
        const cut = at(claims, 'plan').filter(c => c.edit === 'cut');
        expect(covers(cut, text, 'too many')).toBe(true);
        // …and the same words are still the author's. Blue ink under a plan's strike.
        expect(covers(at(claims, 'human'), text, 'too many')).toBe(true);
        // The plan's OWN wording is not the author's — the add side must stay unclaimed.
        expect(covers(at(claims, 'human'), text, 'three times')).toBe(false);
    });

    it('marks a deletion the build made to wording the reader had ACCEPTED', () => {
        // A `del` prints its own ghost rather than covering text on screen, so there is
        // nothing for a plan claim to stack on: without `planned` the sentence the reader
        // agreed to comes back as an anonymous red strike, reading exactly like a line
        // the codebase dropped that nobody ever promised.
        const beforePlan = ft('Uploads', 'It retries five times.');
        const withPlan = ft('Uploads', 'It retries five times. It then backs off.');
        // the build kept the retries and dropped the backing off
        const projected = beforePlan;
        const claims = claimsFor({
            projected, live: projected,
            accepted: { layerId: 'hold:f-1', prev: beforePlan },
            code: { layerId: 'auto:1', prev: withPlan },
        });
        const dels = at(claims, 'code').filter(c => c.edit === 'del');
        expect(dels.length).toBeGreaterThan(0);
        expect(dels.some(c => (c.removed ?? '').includes('backs off') && c.planned)).toBe(true);
    });

    it('does not call an ordinary code deletion planned', () => {
        // The flag is a promise the reader made. With no accepted plan there is no
        // promise, and stamping every removal with one would make the mark meaningless.
        const projected = ft('Uploads', 'It retries five times.');
        const claims = claimsFor({
            projected, live: projected,
            code: { layerId: 'auto:1', prev: ft('Uploads', 'It retries five times. It then backs off.') },
        });
        expect(at(claims, 'code').filter(c => c.edit === 'del')
            .every(c => !c.planned)).toBe(true);
    });

    it('an accepted plan is the PLAN\'s words, never inked as the reader\'s own', () => {
        // The hold set holds both kinds of queued work, and reading a plan-origin hold as
        // `humanBase` put the agent's accepted wording in the author's blue.
        const prev = ft('Uploads', 'It retries five times.');
        const projected = ft('Uploads', 'It retries five times. It then backs off.');
        const claims = claimsFor({ projected, live: projected, accepted: { layerId: 'hold:f-1', prev } });
        expect(at(claims, 'human')).toEqual([]);
        expect(at(claims, 'plan').every(c => c.stage === 'accepted')).toBe(true);
    });

    it('the author\'s own hold stays THEIRS when a proposal lands on top of it', () => {
        // Two parties waiting on one feature. The plan's sentence must not join the
        // author's ink just because the human diff spans it.
        const projected = ft('Uploads', 'It retries five times.');
        const planned = ft('Uploads', 'It retries five times. It then backs off.');
        const claims = claimsFor({
            projected, planned, live: planned,
            humanBase: ft('Uploads', 'It retries twice.'),
            plan: { layerId: 'e-9', stage: 'proposed', runs: [{
                block: { kind: 'para', index: 0 },
                runs: sentenceDiff(projected.paras[0], planned.paras[0]),
            }] },
        });
        const text = planned.paras[0];
        expect(covers(at(claims, 'human'), text, 'backs off')).toBe(false);
        expect(covers(at(claims, 'plan'), text, 'backs off')).toBe(true);
        // …and what the author DID write is still theirs.
        expect(covers(at(claims, 'human'), text, 'five times')).toBe(true);
    });
});
