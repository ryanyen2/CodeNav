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
