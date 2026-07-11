import { describe, it, expect } from 'vitest';
import { ribbonKey, justFinished } from '../webview/tiptap/agent-ribbon';
import type { AgentStep } from '../webview/protocol';

const step = (label: string, done: boolean): AgentStep => ({ label, done });

describe('ribbonKey', () => {
    it('is stable across calls with the same steps', () => {
        const steps = [step('reading a.py', true), step('editing b.py', false)];
        expect(ribbonKey('f1', steps)).toBe(ribbonKey('f1', steps));
    });

    it('changes when a step flips done', () => {
        const before = [step('editing b.py', false)];
        const after = [step('editing b.py', true)];
        expect(ribbonKey('f1', before)).not.toBe(ribbonKey('f1', after));
    });

    it('changes when a new step appends', () => {
        const before = [step('reading a.py', true)];
        const after = [step('reading a.py', true), step('editing b.py', false)];
        expect(ribbonKey('f1', before)).not.toBe(ribbonKey('f1', after));
    });

    it('differs across feature ids for identical steps', () => {
        const steps = [step('reading a.py', true)];
        expect(ribbonKey('f1', steps)).not.toBe(ribbonKey('f2', steps));
    });
});

describe('justFinished', () => {
    it('flags a feature whose steps went non-empty to empty', () => {
        const prev = { f1: [step('editing b.py', false)] };
        expect(justFinished(prev, {})).toEqual(['f1']);
    });

    it('flags a feature absent from cur entirely', () => {
        const prev = { f1: [step('editing b.py', false)] };
        const cur = { f2: [step('reading a.py', false)] };
        expect(justFinished(prev, cur)).toEqual(['f1']);
    });

    it('does not flag a feature still active', () => {
        const prev = { f1: [step('editing b.py', false)] };
        const cur = { f1: [step('editing b.py', true)] };
        expect(justFinished(prev, cur)).toEqual([]);
    });

    it('does not flag a feature that was never active', () => {
        expect(justFinished({}, { f1: [step('editing b.py', false)] })).toEqual([]);
    });

    it('does not flag a feature already empty in prev', () => {
        const prev = { f1: [] as AgentStep[] };
        expect(justFinished(prev, {})).toEqual([]);
    });
});
