import { describe, it, expect } from 'vitest';
import { parseRealize, pendingCodeByFile } from '../state/realize-model';

const SAMPLE = `Some preamble from the realize.txt template.

### 1. NEW FEATURE: "Retry with backoff"
  Intent: Add exponential backoff to the HTTP client.
  Implement this feature in the codebase.

### 2. UPDATE FEATURE: "Sandboxed code execution"
  New intent: Also enforce a memory ceiling.
  Bound code: execution.py::execute_code, execution.py::time_limit
  Edit only: execution.py
  Align the bound code with the new intent.

### 3. RETIRE FEATURE: "Legacy shim"
  Bound code: (no bound code)
  Edit only: (none)
  Remove or refactor this code so the feature no longer exists.
`;

describe('parseRealize', () => {
    it('parses NEW/UPDATE/RETIRE directives with intent, bound code, edit scope', () => {
        const ds = parseRealize(SAMPLE);
        expect(ds.map(d => d.kind)).toEqual(['new', 'update', 'retire']);
        expect(ds[0]).toMatchObject({ title: 'Retry with backoff', boundCode: [], editOnly: [] });
        expect(ds[0].intent).toContain('exponential backoff');
        expect(ds[1].boundCode).toEqual(['execution.py::execute_code', 'execution.py::time_limit']);
        expect(ds[1].editOnly).toEqual(['execution.py']);
        // sentinel bound code / edit only → empty
        expect(ds[2].boundCode).toEqual([]);
        expect(ds[2].editOnly).toEqual([]);
    });

    it('returns [] for empty text', () => {
        expect(parseRealize('')).toEqual([]);
    });

    it('parses ⟨d-id⟩ headings and STEER directives with an Author note', () => {
        const text = [
            '### 1. ⟨d-1a2b3c4d⟩ UPDATE FEATURE: "Color palette"',
            '  New intent: Should expose dark-mode variants.',
            '  Bound code: colors.py::PALETTE',
            '  Edit only: colors.py',
            '',
            '### 2. ⟨d-9f8e7d6c⟩ STEER FEATURE: "Color palette"',
            '  Author note: use CSS custom properties, not a JS map',
            '  Bound code: colors.py::PALETTE',
            '  Edit only: colors.py',
            '',
        ].join('\n');
        const ds = parseRealize(text);
        expect(ds.map(d => d.kind)).toEqual(['update', 'steer']);
        expect(ds[1].title).toBe('Color palette');
        expect(ds[1].intent).toContain('CSS custom properties');
        expect(ds[1].editOnly).toEqual(['colors.py']);
    });
});

describe('pendingCodeByFile', () => {
    it('indexes bound symbols by file, attaching the driving feature title', () => {
        const map = pendingCodeByFile(parseRealize(SAMPLE));
        const exec = map.get('execution.py')!;
        expect(exec.map(c => c.symbol)).toEqual(['execution.py::execute_code', 'execution.py::time_limit']);
        expect(exec[0]).toMatchObject({ title: 'Sandboxed code execution', kind: 'update' });
    });

    it('adds a file-level change when edit-only names a file with no bound symbols', () => {
        const ds = parseRealize(`### 1. UPDATE FEATURE: "X"\n  New intent: y\n  Bound code: (none)\n  Edit only: new_file.py\n`);
        const map = pendingCodeByFile(ds);
        expect(map.get('new_file.py')).toEqual([{ title: 'X', kind: 'update' }]);
    });
});

// ── W3: directive outcomes (.codoc/realized.jsonl) ───────────────────────────
import { parseRealizedLog, newOutcomes } from '../state/realize-model';

describe('W3: parseRealizedLog', () => {
    it('parses outcome lines and skips torn/garbage ones', () => {
        const text = [
            '{"id":"d-1","feature_id":"f-1","kind":"amend","caused_by":"e-9","text":"…","completed_at":"2026-08-03T00:00:00Z","ts":1}',
            '{"id":"d-2","feature_id":"f-2","kind":"add",  "completed_at":"2026-08-03T00:01:00Z"',  // torn
            'not json at all',
            '"a bare string"',
            '{"no_id": true}',
            '{"id":"d-3","feature_id":"f-3","kind":"retire","caused_by":"","text":"","completed_at":"2026-08-03T00:02:00Z","ts":3}',
        ].join('\n');
        const got = parseRealizedLog(text);
        expect(got.map(o => o.id)).toEqual(['d-1', 'd-3']);
        expect(got[0].featureId).toBe('f-1');
        expect(got[0].causedBy).toBe('e-9');
    });

    it('handles empty / missing text', () => {
        expect(parseRealizedLog('')).toEqual([]);
    });
});

describe('W3: newOutcomes', () => {
    it('returns only unseen outcomes, preserving order', () => {
        const entries = parseRealizedLog(
            '{"id":"d-1","feature_id":"f-1"}\n{"id":"d-2","feature_id":"f-2"}\n{"id":"d-3","feature_id":"f-3"}');
        expect(newOutcomes(entries, new Set(['d-1', 'd-3'])).map(o => o.id)).toEqual(['d-2']);
        expect(newOutcomes(entries, new Set()).map(o => o.id)).toEqual(['d-1', 'd-2', 'd-3']);
    });
});
