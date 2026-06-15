/**
 * registry-model.test.ts — pins the host side of the `.codoc/tree.index.json`
 * cross-reference registry (schema mirrored by
 * codoc/codoc_file/render.py:_compute_registry). The Python side already bakes
 * the authoritative `resolved` flag into each ref; the extension only consumes
 * it, so these tests cover the tolerant loader + the pure `isRefResolved` lookup
 * (including the unknown-ref policy and the missing/corrupt-file path).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { isRefResolved, type RegistryData } from '../state/registry-model';
import { loadRegistry } from '../state/registry-loader';

const FIXTURE: RegistryData = {
    version: 1,
    features: {
        'f-1': { title: 'Auth', parent_id: null },
        'f-2': { title: 'Login form', parent_id: 'f-1' },
    },
    bindings: [
        { file: 'auth.py', symbol_path: 'auth.py::Session.login', feature_id: 'f-1' },
        { file: 'forms.py', symbol_path: 'forms.py::LoginForm', feature_id: 'f-2' },
    ],
    refs: [
        // leaf-form ref to a nested binding, resolved by Python.
        { feature_id: 'f-1', label: 'login', file: 'auth.py', symbol: 'login', resolved: true },
        // dead ref — no such symbol in that file.
        { feature_id: 'f-1', label: 'logout', file: 'auth.py', symbol: 'logout', resolved: false },
        // file-only ref, resolved on file presence.
        { feature_id: 'f-2', label: 'forms', file: 'forms.py', symbol: null, resolved: true },
        // file-only ref to an un-indexed file → dead.
        { feature_id: 'f-2', label: 'gone', file: 'missing.py', symbol: null, resolved: false },
    ],
};

function writeRegistry(rootDir: string, data: unknown): void {
    const dir = path.join(rootDir, '.codoc');
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'tree.index.json'), JSON.stringify(data, null, 2));
}

describe('loadRegistry', () => {
    let tmp: string;
    beforeEach(() => { tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'codoc-reg-')); });
    afterEach(() => { fs.rmSync(tmp, { recursive: true, force: true }); });

    it('parses a registry fixture', () => {
        writeRegistry(tmp, FIXTURE);
        const reg = loadRegistry(tmp);
        expect(reg).not.toBeNull();
        expect(reg!.version).toBe(1);
        expect(Object.keys(reg!.features)).toEqual(['f-1', 'f-2']);
        expect(reg!.bindings).toHaveLength(2);
        expect(reg!.refs).toHaveLength(4);
        expect(reg!.refs[0]).toMatchObject({ file: 'auth.py', symbol: 'login', resolved: true });
    });

    it('returns null when the registry file is missing (no throw)', () => {
        expect(loadRegistry(tmp)).toBeNull();
    });

    it('returns null on a corrupt registry file (no throw)', () => {
        const dir = path.join(tmp, '.codoc');
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(path.join(dir, 'tree.index.json'), '{ this is not json');
        expect(loadRegistry(tmp)).toBeNull();
    });

    it('returns null when the JSON is shaped wrong (no refs array)', () => {
        writeRegistry(tmp, { version: 1, features: {}, bindings: [] });
        expect(loadRegistry(tmp)).toBeNull();
    });
});

describe('isRefResolved', () => {
    it('returns false for a ref the registry marks resolved:false', () => {
        expect(isRefResolved(FIXTURE, 'auth.py', 'logout')).toBe(false);
    });

    it('returns true for a ref the registry marks resolved:true', () => {
        expect(isRefResolved(FIXTURE, 'auth.py', 'login')).toBe(true);
    });

    it('treats an unknown ref as resolved (never strike what we do not know)', () => {
        // No entry for (other.py, mystery) in the fixture → unknown → resolved.
        expect(isRefResolved(FIXTURE, 'other.py', 'mystery')).toBe(true);
    });

    it('handles file-only refs (symbol null/empty matches the file-only entry)', () => {
        expect(isRefResolved(FIXTURE, 'forms.py', null)).toBe(true);
        expect(isRefResolved(FIXTURE, 'forms.py', '')).toBe(true);     // '' coalesced to null
        expect(isRefResolved(FIXTURE, 'missing.py', null)).toBe(false);
    });

    it('returns true when the registry is null (graceful — do not strike)', () => {
        expect(isRefResolved(null, 'auth.py', 'logout')).toBe(true);
    });
});
