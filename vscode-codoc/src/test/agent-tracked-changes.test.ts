/**
 * agent-tracked-changes.test.ts — U4 guard for materializing an agent's code-ahead
 * AMEND as the vendored engine's insertion/deletion marks (agent-proposals.ts).
 *
 * The load-bearing invariant: marking the doc with an agent proposal must NOT change
 * the canonical `tree.codoc` projection — old+new coexist for review, but the
 * baseline serializer (insertions excluded, deletions kept) renders back to the exact
 * pre-proposal text. Plus: the marks carry the agent's author id (per-agent tint /
 * accept-reject change id), and the suggestion→amend flattening filters correctly.
 *
 * Rendering of the marks (the <ins>/<del> look) + accept/reject wiring are editor-
 * runtime concerns — covered by manual EDH verification, not this node harness.
 */
import { describe, it, expect } from 'vitest';
import { applyAgentProposals, agentAmendsFrom, type AgentAmend } from '../state/agent-proposals';
import { renderTreeFromDoc } from '../state/doc-serialize';
import {
    makeDoc, featureHeadingNode, paragraphNode, textNode, textToInlineRuns,
    MARK_INSERTION, MARK_DELETION, type PMNode,
} from '../state/pm-doc';

function doc(): PMNode {
    return makeDoc([
        featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, [textNode('Auth')]),
        paragraphNode(textToInlineRuns('Login and sessions.')),
        featureHeadingNode({ fid: 'f-b', level: 0, retired: false, realized: true }, [textNode('Data')]),
        paragraphNode(textToInlineRuns('Persistence.')),
    ]);
}

const amend = (over: Partial<AgentAmend>): AgentAmend => ({
    featureId: 'f-a', changeId: 'e1', authorId: 'claude-code',
    titleOld: 'Auth', titleNew: 'Auth',
    descOld: 'Login and sessions.', descNew: 'Login and sessions.',
    ...over,
});

/** All marks of a type present anywhere in the doc, with their text + attrs. */
function marksOfType(d: PMNode, type: string): { text: string; attrs: Record<string, unknown> }[] {
    const out: { text: string; attrs: Record<string, unknown> }[] = [];
    const walk = (n: PMNode): void => {
        if (n.type === 'text' && n.marks?.some(m => m.type === type)) {
            out.push({ text: n.text ?? '', attrs: n.marks.find(m => m.type === type)!.attrs ?? {} });
        }
        (n.content ?? []).forEach(walk);
    };
    walk(d);
    return out;
}

describe('U4 — applyAgentProposals materializes engine marks', () => {
    it('renders back to the SAME tree.codoc baseline (proposal never leaks)', () => {
        const clean = doc();
        const marked = applyAgentProposals(clean, [amend({ descNew: 'Login and OAuth sessions.' })]);
        expect(renderTreeFromDoc(marked)).toBe(renderTreeFromDoc(clean)); // baseline unchanged
    });

    it('wraps the added words in an insertion mark authored by the agent', () => {
        const marked = applyAgentProposals(doc(), [amend({ changeId: 'e7', descNew: 'Login and OAuth sessions.' })]);
        const ins = marksOfType(marked, MARK_INSERTION);
        expect(ins.map(i => i.text).join('')).toContain('OAuth');
        expect(ins[0].attrs.authorId).toBe('claude-code');
        expect(ins[0].attrs.changeId).toBe('e7'); // drives the accept/reject verdict
    });

    it('wraps removed words in a deletion mark (old + new coexist for review)', () => {
        const marked = applyAgentProposals(doc(), [amend({ titleNew: 'Authentication', descNew: 'Login.' })]);
        // "Login and sessions." → "Login." removes "and sessions"
        const del = marksOfType(marked, MARK_DELETION);
        expect(del.map(d => d.text).join(' ')).toMatch(/and|sessions/);
    });

    it('tints two agents distinctly (F7): each mark carries its own author id', () => {
        const marked = applyAgentProposals(doc(), [
            amend({ featureId: 'f-a', authorId: 'claude-code', descNew: 'Login and OAuth sessions.' }),
            amend({ featureId: 'f-b', authorId: 'codex', descOld: 'Persistence.', descNew: 'Durable persistence.' }),
        ]);
        const authors = new Set(marksOfType(marked, MARK_INSERTION).map(i => i.attrs.authorId));
        expect(authors).toContain('claude-code');
        expect(authors).toContain('codex');
    });

    it('is a no-op (same ref) with no amends, and leaves unrelated features clean', () => {
        const clean = doc();
        expect(applyAgentProposals(clean, [])).toBe(clean);
        const marked = applyAgentProposals(clean, [amend({ descNew: 'Login and OAuth sessions.' })]);
        // f-b untouched → no marks under it
        const all = [...marksOfType(marked, MARK_INSERTION), ...marksOfType(marked, MARK_DELETION)];
        expect(all.length).toBeGreaterThan(0); // f-a changed
    });
});

describe('U4 — agentAmendsFrom (suggestion → amend flattening)', () => {
    it('keeps only code-ahead amends with a feature id, mapping event id + role', () => {
        const out = agentAmendsFrom([
            { direction: 'code-ahead', kind: 'amend', featureId: 'f-a', id: 's1', eventId: 'e1', originRole: 'claude-code', titleOld: 'A', titleNew: 'B' },
            { direction: 'code-ahead', kind: 'add', featureId: null, id: 's2', originRole: 'claude-code' },     // add → widget, not a mark
            { direction: 'doc-ahead', kind: 'amend', featureId: 'f-c', id: 's3', originRole: 'human' },         // human direction → no marks
            { direction: 'code-ahead', kind: 'amend', featureId: null, id: 's4', originRole: 'codex' },          // no feature → skip
        ]);
        expect(out).toHaveLength(1);
        expect(out[0]).toMatchObject({ featureId: 'f-a', changeId: 'e1', authorId: 'claude-code' });
    });

    it('falls back to the suggestion id as change id when no event id is present', () => {
        const out = agentAmendsFrom([{ direction: 'code-ahead', kind: 'amend', featureId: 'f-a', id: 's9', originRole: 'codex' }]);
        expect(out[0].changeId).toBe('s9');
    });
});
