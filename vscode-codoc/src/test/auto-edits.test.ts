import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import {
    displacedHuman, editKey, unseenEdits, pruneSeen, catchUpLabel,
    keepAllLabel, keepAllVerdicts,
} from '../state/auto-edits';
import type { AutoEdit } from '../state/bindings-model';

const edit = (over: Partial<AutoEdit> = {}): AutoEdit => ({
    at: '1', prev: 'old prose', written_by: 'loop', rationale: '', ...over,
});

describe('weighting — whose words were displaced', () => {
    it('the loop revising its own bootstrap prose reads as housekeeping', () => {
        expect(displacedHuman(edit({ written_by: 'loop' }))).toBe(false);
    });

    it("a rewrite of the reader's own words is named as theirs", () => {
        expect(displacedHuman(edit({ written_by: 'human' }))).toBe(true);
    });

    it('an agent counts as not-the-reader — it is not the person being surprised', () => {
        expect(displacedHuman(edit({ written_by: 'claude-code' }))).toBe(false);
    });

    it('a legacy row with no authorship degrades to the quiet reading', () => {
        expect(displacedHuman(edit({ written_by: '' }))).toBe(false);
    });
});

describe('the seen-set is keyed per REWRITE, not per feature', () => {
    it('a later rewrite of the same feature is unseen again', () => {
        const first = edit({ at: '1' });
        const later = edit({ at: '2' });
        const seen = new Set([editKey('f-a', first)]);
        expect(unseenEdits({ 'f-a': first }, seen, ['f-a'])).toEqual([]);
        // the loop came back and rewrote it a second time — that is news
        expect(unseenEdits({ 'f-a': later }, seen, ['f-a'])).toHaveLength(1);
    });

    it('returns the unseen ones in the order they were asked for (document order)', () => {
        const edits = { 'f-b': edit({ at: '1' }), 'f-a': edit({ at: '1' }) };
        expect(unseenEdits(edits, new Set(), ['f-a', 'f-b']).map(u => u.fid))
            .toEqual(['f-a', 'f-b']);
    });

    it('skips features with no rewrite rather than throwing on the gap', () => {
        expect(unseenEdits({ 'f-a': edit() }, new Set(), ['f-missing', 'f-a']))
            .toHaveLength(1);
    });
});

describe('pruneSeen keeps the acknowledgement set from growing forever', () => {
    it('drops keys whose rewrite is no longer offered', () => {
        const e = edit({ at: '1' });
        const seen = new Set([editKey('f-a', e), editKey('f-gone', e)]);
        expect([...pruneSeen(seen, { 'f-a': e })]).toEqual([editKey('f-a', e)]);
    });

    it('drops the acknowledgement when the SAME feature gets a newer rewrite', () => {
        const seen = new Set([editKey('f-a', edit({ at: '1' }))]);
        expect(pruneSeen(seen, { 'f-a': edit({ at: '2' }) }).size).toBe(0);
    });

    it('empties out when nothing is pending', () => {
        expect(pruneSeen(new Set(['f-a@1']), {}).size).toBe(0);
    });
});

describe('the catch-up line spends words only on the distinction that matters', () => {
    it('says nothing at all when there is nothing to catch up on', () => {
        expect(catchUpLabel([])).toBe('');
    });

    it('counts plainly when the loop only revised its own prose', () => {
        expect(catchUpLabel([{ edit: edit() }, { edit: edit() }]))
            .toBe('codoc rewrote 2 descriptions');
        expect(catchUpLabel([{ edit: edit() }])).toBe('codoc rewrote 1 description');
    });

    it("names it as YOURS when the reader's own wording was displaced", () => {
        const mine = { edit: edit({ written_by: 'human' }) };
        expect(catchUpLabel([mine])).toBe('codoc edited your wording');
        expect(catchUpLabel([mine, mine])).toBe('codoc edited your wording in 2 places');
    });

    it('separates the two when the batch is mixed', () => {
        expect(catchUpLabel([{ edit: edit({ written_by: 'human' }) }, { edit: edit() }]))
            .toBe('codoc rewrote 2 descriptions (1 of yours)');
    });
});

describe('keeping the lot at once', () => {
    const unseen = (...kinds: string[]) =>
        kinds.map((written_by, i) => ({ fid: `f${i}`, edit: edit({ at: String(i), written_by }) }));

    it('says how many there are', () => {
        expect(keepAllLabel(unseen('loop', 'loop', 'loop'))).toBe('✓ Keep all (3)');
    });

    it("names the ones that replaced the reader's own wording", () => {
        // The one thing a bulk verdict can hide. Accept-all names the proposals
        // that ask the agent to write code; this names the rewrites of your prose.
        expect(keepAllLabel(unseen('loop', 'human', 'human'))).toBe('✓ Keep all (3, 2 of your wording)');
    });

    it('says nothing when there is nothing owed', () => {
        expect(keepAllLabel([])).toBe('');
    });

    it('sends one keep per rewrite, and keeps every one of them', () => {
        // Keep is the verdict that changes nothing, which is why it may be given
        // in bulk at all. If any of these came out as a Restore it would rewrite
        // a description nobody asked it to.
        const rows = unseen('loop', 'human');
        const out = keepAllVerdicts(rows);
        expect(out).toHaveLength(2);
        expect(out.every(v => v.keep)).toBe(true);
        expect(out.map(v => v.fid)).toEqual(['f0', 'f1']);
    });

    it('carries each rewrite own HLC, so a later rewrite is still unseen', () => {
        // The seen-set is keyed by fid@at. Sending the wrong `at` would mark a
        // rewrite that has not happened yet as already acknowledged.
        const rows = [
            { fid: 'a', edit: edit({ at: '7', prev: 'was A' }) },
            { fid: 'b', edit: edit({ at: '9', prev: 'was B' }) },
        ];
        expect(keepAllVerdicts(rows)).toEqual([
            { fid: 'a', at: '7', keep: true, prev: 'was A' },
            { fid: 'b', at: '9', keep: true, prev: 'was B' },
        ]);
    });

    it('does nothing when nothing is owed', () => {
        expect(keepAllVerdicts([])).toEqual([]);
    });
});

describe('the rewrite surface stands down once the reader has edited the feature', () => {
    const docWith = async (fid: string, text: string) => {
        const { codocSchema } = await import('../webview/tiptap/schema');
        return codocSchema().nodeFromJSON({
            type: 'doc',
            content: [
                { type: 'featureHeading', attrs: { fid, level: 0, retired: false, realized: true },
                  content: [{ type: 'text', text: 'Sessions' }] },
                { type: 'paragraph', content: [{ type: 'text', text }] },
            ],
        });
    };
    const unseen = { 'f-1': edit({ prev: 'the old wording here' }) };

    it('draws the Keep / Restore surface when the rewrite is the latest word', async () => {
        const { buildAutoEditDecorations } = await import('../webview/tiptap/auto-edit-decorations');
        const doc = await docWith('f-1', 'the new wording here');
        expect(buildAutoEditDecorations(doc, unseen).find().length).toBeGreaterThan(0);
    });

    it('draws nothing once the reader has rewritten that feature themselves', async () => {
        // Correctness, not decluttering: "Restore mine" re-authors the wording the loop
        // displaced, and once the author has edited the same feature that wording is two
        // revisions stale — restoring it would discard their newer text. The verdict
        // would be on words that are no longer there.
        const { buildAutoEditDecorations } = await import('../webview/tiptap/auto-edit-decorations');
        const doc = await docWith('f-1', 'the new wording here');
        const edited = new Set(['f-1']);
        expect(buildAutoEditDecorations(doc, unseen, undefined, edited).find()).toEqual([]);
    });

    it('leaves other features alone', async () => {
        const { buildAutoEditDecorations } = await import('../webview/tiptap/auto-edit-decorations');
        const doc = await docWith('f-1', 'the new wording here');
        const edited = new Set(['f-other']);
        expect(buildAutoEditDecorations(doc, unseen, undefined, edited).find().length).toBeGreaterThan(0);
    });
});

describe('the loop diff no longer needs to stand down — the model separates the words', () => {
    // The diff used to be "what it said before" against "what is on screen", which is
    // only the loop's rewrite while the screen still holds the loop's words. Once the
    // author edited the same paragraph their words sat inside `current`, so the
    // underline claimed the loop had written them and the strikethrough offered to
    // restore a version two revisions old. A participant hit exactly that.
    //
    // Two heuristics fenced it off — a `locallyEdited` stand-down and an `arrivedAs`
    // memo of the first render — and both are gone, because the settlement model
    // answers the question they were approximating: a code claim is computed against
    // the projection, carried forward through the author's own diff, and voided when
    // the author has edited inside the sentence it reports.

    it('does not carry the diff, or the workarounds it needed', () => {
        // Comments stripped first: the module's header names both by way of explaining
        // what went and why, and a source grep that cannot tell an explanation from a
        // call would fail on the documentation of its own fix.
        const src = readFileSync(
            resolve(__dirname, '../webview/tiptap/auto-edit-decorations.ts'), 'utf8')
            .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
        expect(src).not.toMatch(/arrivedAs/);
        expect(src).not.toMatch(/reviewDiffSpans/);
        expect(src).not.toMatch(/ce-autoedit-add/);
    });

    it('voids a code claim on a sentence the author has rewritten', async () => {
        const { claimsFor } = await import('../state/settlement');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: { title: 'T', paras: ['the old wording here'] } },
            projected: { title: 'T', paras: ['the new wording here'] },
            live: { title: 'T', paras: ['something the author typed instead'] },
        });
        expect(claims.some(c => c.channel === 'code' && c.edit === 'add')).toBe(false);
    });

    it('still reports the diff at the SENTENCE, which is the unit of the decision', async () => {
        // A word diff of a rewritten claim shreds both versions into alternating
        // fragments and the reader has to reassemble two sentences before they can
        // agree with either.
        const { claimsFor } = await import('../state/settlement');
        const live = { title: 'T', paras: ['It uses LanceDB. It caches results.'] };
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: { title: 'T', paras: ['It uses FAISS. It caches results.'] } },
            projected: live, live,
        });
        const add = claims.find(c => c.channel === 'code' && c.edit === 'add')!;
        const marked = live.paras[0].slice(add.start, add.end);
        expect(marked).toContain('It uses LanceDB.');      // the whole sentence
        expect(marked).not.toContain('caches');            // and not its neighbour
    });
});
