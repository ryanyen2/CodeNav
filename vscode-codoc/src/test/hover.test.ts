/**
 * hover.test.ts — pins the pure hover-card resolver `resolveCard`
 * (registry-model.ts), the vscode-free core behind the raw-text HoverProvider
 * and (later) the webview popover. The `provideHover` glue is thin vscode and
 * not unit-tested here — all the logic lives in the pure resolver below.
 *
 * Covers: a resolved symbol ref → HoverCard with title/count; an unrealized
 * placeholder → count suppressed + plan flag; a file-only ref → FileOwnersCard
 * enumerating owners; a dead ref → DeadRef with the broken target; and gist null
 * when no description is threaded in (the sidecar has none today).
 */
import { describe, it, expect } from 'vitest';
import { resolveCard, type RegistryData } from '../state/registry-model';
import { SidecarData } from '../state/bindings-model';

const REGISTRY: RegistryData = {
    version: 1,
    features: {
        'f-1': { title: 'Auth', parent_id: null },
        'f-2': { title: 'Login form', parent_id: 'f-1' },
        'f-3': { title: 'Planned export', parent_id: null },
    },
    bindings: [
        // leaf-form ref `login` must resolve against this qualified path.
        { file: 'auth.py', symbol_path: 'auth.py::Session.login', feature_id: 'f-1' },
        { file: 'forms.py', symbol_path: 'forms.py::LoginForm', feature_id: 'f-2' },
    ],
    refs: [
        { feature_id: 'f-1', label: 'login', file: 'auth.py', symbol: 'login', resolved: true },
        { feature_id: 'f-1', label: 'logout', file: 'auth.py', symbol: 'logout', resolved: false },
        { feature_id: 'f-2', label: 'forms', file: 'forms.py', symbol: null, resolved: true },
        { feature_id: 'f-3', label: 'export', file: 'export.py', symbol: 'run', resolved: true },
    ],
};

const SIDECAR: SidecarData = {
    version: 4,
    by_feature: {
        'f-1': [{ file: 'auth.py', symbol: 'auth.py::Session.login' }, { file: 'auth.py', symbol: 'auth.py::Session.logout' }],
        'f-2': [{ file: 'forms.py', symbol: 'forms.py::LoginForm' }],
        'f-3': [],
    },
    by_file: {
        'auth.py': [
            { symbol: 'auth.py::Session.login', feature_id: 'f-1', feature_title: 'Auth' },
            { symbol: 'auth.py::Session.logout', feature_id: 'f-1', feature_title: 'Auth' },
        ],
        'forms.py': [
            { symbol: 'forms.py::LoginForm', feature_id: 'f-2', feature_title: 'Login form' },
        ],
    },
    features: {
        'f-1': { title: 'Auth', parent_id: null, realized: true },
        'f-2': { title: 'Login form', parent_id: 'f-1', realized: true },
        'f-3': { title: 'Planned export', parent_id: null, realized: false },
    },
};

describe('resolveCard — symbol refs', () => {
    it('resolves a leaf-form ref to its owning feature with a binding count', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'auth.py', 'login');
        expect(card.resolved).toBe(true);
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature HoverCard');
        expect(card.title).toBe('Auth');
        expect(card.ownerFeatureId).toBe('f-1');
        expect(card.bindingCount).toBe(2); // f-1 owns login + logout
        expect(card.unrealized).toBe(false);
    });

    it('gist is null when no description is threaded in (sidecar has none)', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'auth.py', 'login');
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature card');
        expect(card.gist).toBeNull();
    });

    it('derives a one-line gist from a threaded description', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'auth.py', 'login', 'Handles login. Plus more.');
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature card');
        expect(card.gist).toBe('Handles login.');
    });
});

describe('resolveCard — gist prefers the sidecar pitch', () => {
    // A sidecar carrying the Python-derived pitch (refs flattened, ≤120 chars).
    const PITCHED: SidecarData = {
        ...SIDECAR,
        features: {
            ...SIDECAR.features,
            'f-1': { title: 'Auth', parent_id: null, realized: true, pitch: 'Handles authentication and sessions.' },
        },
    };

    it('uses the owner feature pitch even when a divergent description is threaded', () => {
        // The threaded description would yield a different (raw/markdown) sentence;
        // the pitch must win so the hover matches the overview/glance pitch.
        const card = resolveCard(REGISTRY, PITCHED, 'auth.py', 'login', '[login](codoc:auth.py#login) is the entrypoint.');
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature card');
        expect(card.gist).toBe('Handles authentication and sessions.');
    });

    it('falls back to firstSentence when the owner has no pitch (backward-compat)', () => {
        // f-1 has no pitch in the default SIDECAR → the threaded description is used.
        const card = resolveCard(REGISTRY, SIDECAR, 'auth.py', 'login', 'Login flow. Extra.');
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature card');
        expect(card.gist).toBe('Login flow.');
    });
});

describe('resolveCard — unrealized placeholder', () => {
    it('suppresses the count and flags unrealized for a plan placeholder', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'export.py', 'run');
        expect(card.resolved).toBe(true);
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature card');
        expect(card.ownerFeatureId).toBe('f-3');
        expect(card.unrealized).toBe(true);
        expect(card.bindingCount).toBe(0);
    });
});

describe('resolveCard — file-only refs', () => {
    it('enumerates the file owning features (names none arbitrarily)', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'forms.py', null);
        expect(card.resolved).toBe(true);
        if (!card.resolved || card.kind !== 'file') throw new Error('expected a FileOwnersCard');
        expect(card.file).toBe('forms.py');
        expect(card.owners).toEqual([{ featureId: 'f-2', title: 'Login form' }]);
    });

    it('de-dups owners that own several symbols in the file', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'auth.py', null);
        if (!card.resolved || card.kind !== 'file') throw new Error('expected a FileOwnersCard');
        // auth.py has two symbols, both owned by f-1 → a single owner.
        expect(card.owners).toEqual([{ featureId: 'f-1', title: 'Auth' }]);
    });

    it('an empty symbol string is treated as a file-only ref', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'forms.py', '');
        expect(card.resolved).toBe(true);
        if (!card.resolved) throw new Error('expected resolved');
        expect(card.kind).toBe('file');
    });
});

describe('resolveCard — dead refs', () => {
    it('returns a DeadRef with the broken file#symbol target', () => {
        const card = resolveCard(REGISTRY, SIDECAR, 'auth.py', 'logout');
        expect(card.resolved).toBe(false);
        if (card.resolved) throw new Error('expected a DeadRef');
        expect(card.target).toBe('auth.py#logout');
    });

    it('builds a file-only target for a dead file-only ref', () => {
        const reg: RegistryData = {
            ...REGISTRY,
            refs: [{ feature_id: 'f-2', label: 'gone', file: 'missing.py', symbol: null, resolved: false }],
        };
        const card = resolveCard(reg, SIDECAR, 'missing.py', null);
        expect(card.resolved).toBe(false);
        if (card.resolved) throw new Error('expected a DeadRef');
        expect(card.target).toBe('missing.py');
    });
});

describe('resolveCard — graceful fallbacks', () => {
    it('falls back to the sidecar by_file when no registry is loaded', () => {
        const card = resolveCard(null, SIDECAR, 'auth.py', 'login');
        expect(card.resolved).toBe(true);
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature card');
        expect(card.ownerFeatureId).toBe('f-1');
    });

    it('resolves the owner via the registry refs entry even with no binding', () => {
        // An unrealized placeholder authors a ref but owns no binding yet; the
        // registry's refs entry still carries the authoritative feature_id.
        const reg: RegistryData = {
            ...REGISTRY,
            bindings: [],
            refs: [{ feature_id: 'f-3', label: 'run', file: 'export.py', symbol: 'run', resolved: true }],
        };
        const card = resolveCard(reg, SIDECAR, 'export.py', 'run');
        expect(card.resolved).toBe(true);
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected a feature card');
        expect(card.ownerFeatureId).toBe('f-3');
        expect(card.unrealized).toBe(true);
    });

    it('returns a DeadRef when registry-less and no by_file entry matches', () => {
        // No registry to consult and the sidecar by_file has no match → present
        // as dead rather than an empty card.
        const card = resolveCard(null, SIDECAR, 'ghost.py', 'ghost');
        expect(card.resolved).toBe(false);
        if (card.resolved) throw new Error('expected a DeadRef');
        expect(card.target).toBe('ghost.py#ghost');
    });
});
