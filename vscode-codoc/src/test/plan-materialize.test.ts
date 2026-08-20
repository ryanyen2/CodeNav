import { describe, it, expect } from 'vitest';
import { materializePlan, insertAt, subtreeEnd, planRuns, descParas, type PlanNode } from '../state/plan-materialize';
import { featureUnits, commandsForSettle } from '../state/commands-from-doc';
import { renderTreeFromDoc } from '../state/doc-serialize';
import {
    makeDoc, featureHeadingNode, paragraphNode, textNode, textToInlineRuns,
    NODE_FEATURE_HEADING, type PMNode, type FeatureHeadingAttrs,
} from '../state/pm-doc';

const head = (fid: string | null, title: string, level = 0, extra: Partial<FeatureHeadingAttrs> = {}): PMNode =>
    featureHeadingNode({ fid, level, retired: false, realized: true, ...extra }, [textNode(title)]);

const para = (text: string, owner: string | null = null): PMNode =>
    paragraphNode(textToInlineRuns(text), owner);

/** A small real tree: two roots, the first with a child, each with prose. */
const tree = (): PMNode => makeDoc([
    head('f-1', 'Uploads'), para('How uploading works.', 'f-1'),
    head('f-2', 'Retries', 1), para('It retries twice.', 'f-2'),
    head('f-3', 'Downloads'), para('How downloading works.', 'f-3'),
]);

const add = (over: Partial<PlanNode> = {}): PlanNode => ({
    kind: 'add', changeId: 'e-add', authorId: 'claude-code',
    parentId: null, title: 'Backoff', description: 'It waits longer each time.', ...over,
});

/** A heading's attrs, typed — the node shape stores them loosely. */
const attrsOf = (b: PMNode): Partial<FeatureHeadingAttrs> =>
    (b.attrs ?? {}) as Partial<FeatureHeadingAttrs>;

const headingsOf = (doc: PMNode): string[] =>
    (doc.content ?? []).filter(b => b.type === NODE_FEATURE_HEADING)
        .map(b => (b.content ?? []).map(n => n.text ?? '').join(''));

describe('a planned node is written where it will land', () => {
    it('puts the node in the document, not beside it', () => {
        const doc = materializePlan(tree(), [add({ parentId: 'f-1', afterId: 'f-2' })]);
        expect(headingsOf(doc)).toEqual(['Uploads', 'Retries', 'Backoff', 'Downloads']);
    });

    it('lands after the anchor\'s whole subtree, the way accepting will place it', () => {
        // after f-1 means after Retries too — a sibling anchor names the node, not the
        // gap under its title line.
        const doc = materializePlan(tree(), [add({ afterId: 'f-1' })]);
        expect(headingsOf(doc)).toEqual(['Uploads', 'Retries', 'Backoff', 'Downloads']);
    });

    it('honours a before-anchor', () => {
        const doc = materializePlan(tree(), [add({ beforeId: 'f-3' })]);
        expect(headingsOf(doc)).toEqual(['Uploads', 'Retries', 'Backoff', 'Downloads']);
    });

    it('flags it proposed and carries the proposal id', () => {
        const doc = materializePlan(tree(), [add({ beforeId: 'f-3' })]);
        const node = (doc.content ?? []).find(b => b.type === NODE_FEATURE_HEADING
            && attrsOf(b).proposed)!;
        expect(attrsOf(node).proposed).toBe('e-add');
        expect(attrsOf(node).fid).toBeNull();
    });

    it('nests under its parent by level', () => {
        const doc = materializePlan(tree(), [add({ parentId: 'f-1' })]);
        const node = (doc.content ?? []).find(b => b.type === NODE_FEATURE_HEADING
            && attrsOf(b).proposed)!;
        expect(attrsOf(node).level).toBe(1);
    });

    it('is a no-op with no plans, and never mutates the baseline it is given', () => {
        const base = tree();
        const before = JSON.stringify(base);
        expect(materializePlan(base, [])).toBe(base);
        materializePlan(base, [add()]);
        expect(JSON.stringify(base)).toBe(before);
    });
});

describe('a planned node is never the human\'s', () => {
    it('emits no command — the settle does not author the agent\'s proposal', () => {
        const baseline = tree();
        const withPlan = materializePlan(baseline, [add({ beforeId: 'f-3' })]);
        expect(featureUnits(withPlan).map(u => u.title)).toEqual(['Uploads', 'Retries', 'Downloads']);
        const cmds = commandsForSettle(featureUnits(baseline), featureUnits(withPlan), 't-1');
        expect(cmds).toEqual([]);
    });

    it('does not reach tree.codoc — an unanswered suggestion is not a decision', () => {
        const baseline = tree();
        const withPlan = materializePlan(baseline, [add({ beforeId: 'f-3' })]);
        expect(renderTreeFromDoc(withPlan)).toBe(renderTreeFromDoc(baseline));
    });

    it('does not steal the prose of the feature it lands next to', () => {
        // The hazard: paragraphs route by position where no ownerId is stamped, so a
        // node dropped mid-description would capture them — and a planned node's
        // contents are discarded, so they would vanish from the real set_description.
        const bare = makeDoc([
            head('f-1', 'Uploads'), para('Line one.'), para('Line two.'),
            head('f-3', 'Downloads'), para('How downloading works.'),
        ]);
        const withPlan = materializePlan(bare, [add({ afterId: 'f-1' })]);
        const units = featureUnits(withPlan);
        expect(units.find(u => u.fid === 'f-1')!.description).toBe('Line one.\n\nLine two.');
        expect(commandsForSettle(featureUnits(bare), featureUnits(withPlan), 't-1')).toEqual([]);
    });

    it('owns its own paragraphs explicitly, so routing never falls back to position', () => {
        const doc = materializePlan(tree(), [add({ beforeId: 'f-3' })]);
        const blocks = doc.content ?? [];
        const at = blocks.findIndex(b => b.type === NODE_FEATURE_HEADING
            && attrsOf(b).proposed);
        expect((blocks[at + 1].attrs as { ownerId?: string }).ownerId).toBe('e-add');
    });
});

describe('a planned retire strikes the words it proposes to remove', () => {
    const retire: PlanNode = {
        kind: 'retire', changeId: 'e-ret', authorId: 'claude-code',
        featureId: 'f-2', title: 'Retries', description: 'It retries twice.',
    };

    it('marks the node\'s own text rather than inserting anything', () => {
        const doc = materializePlan(tree(), [retire]);
        expect(headingsOf(doc)).toEqual(['Uploads', 'Retries', 'Downloads']);
        const node = (doc.content ?? []).find(b => b.type === NODE_FEATURE_HEADING
            && attrsOf(b).fid === 'f-2')!;
        expect((node.content ?? [])[0].marks?.some(m => m.type === 'deletion')).toBe(true);
    });

    it('leaves every other feature alone', () => {
        const doc = materializePlan(tree(), [retire]);
        const other = (doc.content ?? []).find(b => b.type === NODE_FEATURE_HEADING
            && attrsOf(b).fid === 'f-1')!;
        expect((other.content ?? [])[0].marks ?? []).toEqual([]);
    });

    it('still emits no command — a proposed retire is not the ~ gesture', () => {
        const baseline = tree();
        const withPlan = materializePlan(baseline, [retire]);
        expect(commandsForSettle(featureUnits(baseline), featureUnits(withPlan), 't-1')).toEqual([]);
    });
});

describe('several plans at once', () => {
    it('places each at its own anchor — an earlier insertion never shifts a later one', () => {
        const doc = materializePlan(tree(), [
            add({ changeId: 'e-a', title: 'Alpha', beforeId: 'f-3' }),
            add({ changeId: 'e-b', title: 'Beta', afterId: 'f-3' }),
        ]);
        expect(headingsOf(doc)).toEqual(['Uploads', 'Retries', 'Alpha', 'Downloads', 'Beta']);
    });
});

describe('anchoring helpers', () => {
    it('finds the end of a subtree, not the next block', () => {
        const blocks = tree().content ?? [];
        expect(subtreeEnd(blocks, 0)).toBe(4);   // past Uploads AND its child Retries
    });

    it('falls back to the document end when no anchor resolves', () => {
        const blocks = tree().content ?? [];
        expect(insertAt(blocks, null, 'f-missing', 'f-also-missing')).toBe(blocks.length);
    });
});

describe('planRuns — what the settlement layer is handed', () => {
    it('makes a whole planned add read as one insertion per block', () => {
        const runs = planRuns('add', '', 'Backoff', [], descParas('One.\n\nTwo.'));
        expect(runs.map(r => r.runs.map(x => x.t))).toEqual([['ins'], ['ins'], ['ins']]);
        expect(runs.length).toBe(3);
    });

    it('makes a planned retire read as one deletion per block', () => {
        const runs = planRuns('retire', 'Retries', '', descParas('It retries twice.'), []);
        expect(runs.every(r => r.runs.every(x => x.t === 'del'))).toBe(true);
    });

    it('diffs an amend by word in the title and by sentence in the prose', () => {
        const runs = planRuns('amend', 'Retry policy', 'Retry budget',
            descParas('It retries twice. It gives up.'),
            descParas('It retries five times. It gives up.'));
        expect(runs[0].runs.some(r => r.t === 'ins' && r.s.includes('budget'))).toBe(true);
        const prose = runs[1].runs;
        expect(prose.some(r => r.t === 'ins' && r.s.includes('five times'))).toBe(true);
        expect(prose.some(r => r.t === 'same' && r.s.includes('gives up'))).toBe(true);
    });

    it('gives a multi-paragraph amend ONE block per paragraph, matching the document', () => {
        // The bug this pins: `planRuns` used to split its own argument on `\n\n`, and
        // the caller handed it DISPLAY-space text where every newline is already one
        // atom char — so the split never matched, the whole description became a single
        // planned block against the editor's several, and every paragraph after the
        // first came back unpaired and was inked as text the author had just typed.
        const runs = planRuns('amend', 'T', 'T',
            descParas('One.\n\nTwo.'), descParas('One changed.\n\nTwo.'));
        expect(runs.map(r => r.block)).toEqual([
            { kind: 'title' }, { kind: 'para', index: 0 }, { kind: 'para', index: 1 },
        ]);
        // …and each block's runs concatenate back to exactly that paragraph's old+new,
        // which is what `settlement-stages.concatRuns` relies on to build `planned`.
        expect(runs[2].runs.map(r => r.s).join('')).toBe('Two.');
    });
});
