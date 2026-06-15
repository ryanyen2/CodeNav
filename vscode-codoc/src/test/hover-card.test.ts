/**
 * hover-card.test.ts — pins the PURE webview hover-card logic (U4):
 *   1. `cardModel` (hover-card.ts) — the DOM-free distillation of a `ResolvedCard`
 *      into the exact display fields the card renders (title / gist-fallback /
 *      count-suppression / file-owners / dead-ref). `buildCardDom` is a thin DOM
 *      projection over this, so the branching logic is tested without a DOM (the
 *      vitest env is `node`).
 *   2. `buildHoverCards` (registry-model.ts) — the host-side payload assembly that
 *      precomputes every ref + feature card from the registry + sidecar (the
 *      webview can't read files / call Python), threading the description gist in.
 *
 * Covers the same card states as the raw-text hover (hover.test.ts): a resolved
 * symbol card, the gist fallback, the unrealized "plan" suppression, the file-owners
 * card, and the dead-ref state — at the webview layer this time.
 */
import { describe, it, expect } from 'vitest';
import { cardModel } from '../webview/tiptap/hover-card';
import { buildHoverCards, refKey, type ResolvedCard, type RegistryData } from '../state/registry-model';
import { SidecarData } from '../state/bindings-model';

// ── cardModel: ResolvedCard → display fields ──────────────────────────────────

describe('cardModel — feature card', () => {
    it('keeps the title + gist + "N refs" meta for a resolved feature', () => {
        const card: ResolvedCard = {
            resolved: true, kind: 'feature', title: 'Auth', gist: 'Handles login.',
            bindingCount: 2, ownerFeatureId: 'f-1', unrealized: false,
        };
        const m = cardModel(card);
        expect(m.state).toBe('feature');
        expect(m.title).toBe('Auth');
        expect(m.gist).toBe('Handles login.');
        expect(m.gistMuted).toBe(false);
        expect(m.meta).toBe('2 refs');
        expect(m.plan).toBe(false);
        expect(m.openLabel).toBe('Open code');
    });

    it('singularizes the ref count', () => {
        const m = cardModel({ resolved: true, kind: 'feature', title: 'X', gist: 'g', bindingCount: 1, ownerFeatureId: 'f', unrealized: false });
        expect(m.meta).toBe('1 ref');
    });

    it('falls back to muted "No description yet" when gist is null', () => {
        const m = cardModel({ resolved: true, kind: 'feature', title: 'Auth', gist: null, bindingCount: 0, ownerFeatureId: 'f-1', unrealized: false });
        expect(m.gist).toBe('No description yet');
        expect(m.gistMuted).toBe(true);
    });

    it('suppresses the count and shows a plan marker for an unrealized placeholder', () => {
        const m = cardModel({ resolved: true, kind: 'feature', title: 'Planned', gist: null, bindingCount: 0, ownerFeatureId: 'f-3', unrealized: true });
        expect(m.plan).toBe(true);
        expect(m.meta).toBe('◇ plan');
        expect(m.meta).not.toMatch(/ref/);
    });
});

describe('cardModel — file card', () => {
    it('enumerates the owning features and a "used by N" meta', () => {
        const card: ResolvedCard = {
            resolved: true, kind: 'file', file: 'auth.py',
            owners: [{ featureId: 'f-1', title: 'Auth' }, { featureId: 'f-2', title: 'Session' }],
        };
        const m = cardModel(card);
        expect(m.state).toBe('file');
        expect(m.title).toBe('auth.py');
        expect(m.meta).toBe('used by 2 features');
        expect(m.owners).toEqual(['Auth', 'Session']);
        expect(m.openLabel).toBe('Open file');
    });

    it('handles a file with no owners (muted fallback)', () => {
        const m = cardModel({ resolved: true, kind: 'file', file: 'x.py', owners: [] });
        expect(m.meta).toBe('used by 0 features');
        expect(m.owners).toEqual([]);
        expect(m.gist).toBe('No owning features yet');
        expect(m.gistMuted).toBe(true);
    });
});

describe('cardModel — dead ref', () => {
    it('produces the unresolved state with the broken target + no navigate', () => {
        const m = cardModel({ resolved: false, target: 'auth.py#gone' });
        expect(m.state).toBe('dead');
        expect(m.title).toBe('Unresolved reference');
        expect(m.target).toBe('auth.py#gone');
        expect(m.note).toMatch(/Connections panel/);
        expect(m.openLabel).toBeNull();
    });
});

// ── buildHoverCards: host-side payload assembly ───────────────────────────────

const REGISTRY: RegistryData = {
    version: 1,
    features: {
        'f-1': { title: 'Auth', parent_id: null },
        'f-2': { title: 'Login form', parent_id: 'f-1' },
        'f-3': { title: 'Planned export', parent_id: null },
    },
    bindings: [
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
        'f-1': [{ file: 'auth.py', symbol: 'auth.py::Session.login' }],
        'f-2': [{ file: 'forms.py', symbol: 'forms.py::LoginForm' }],
        'f-3': [],
    },
    by_file: {
        'auth.py': [{ symbol: 'auth.py::Session.login', feature_id: 'f-1', feature_title: 'Auth' }],
        'forms.py': [{ symbol: 'forms.py::LoginForm', feature_id: 'f-2', feature_title: 'Login form' }],
    },
    features: {
        'f-1': { title: 'Auth', parent_id: null, realized: true },
        'f-2': { title: 'Login form', parent_id: 'f-1', realized: true },
        'f-3': { title: 'Planned export', parent_id: null, realized: false },
    },
} as SidecarData;

const DESC: Record<string, string> = {
    'f-1': 'Handles authentication. More detail here.',
    'f-2': 'Renders the login form.',
    'f-3': '',
};

describe('buildHoverCards — byRef', () => {
    const cards = buildHoverCards(REGISTRY, SIDECAR, fid => DESC[fid] ?? null);

    it('keys a symbol ref by file#symbol with the owning-feature card + threaded gist', () => {
        const card = cards.byRef[refKey('auth.py', 'login')];
        expect(card).toBeTruthy();
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.title).toBe('Auth');
        expect(card.ownerFeatureId).toBe('f-1');
        // gist threaded from the owning feature description (sidecar has none).
        expect(card.gist).toBe('Handles authentication.');
    });

    it('keys a file-only ref by bare file → a file-owners card', () => {
        const card = cards.byRef['forms.py'];
        expect(card).toBeTruthy();
        if (!card.resolved || card.kind !== 'file') throw new Error('expected file card');
        expect(card.file).toBe('forms.py');
        expect(card.owners).toEqual([{ featureId: 'f-2', title: 'Login form' }]);
    });

    it('marks a dead ref unresolved with its target', () => {
        const card = cards.byRef[refKey('auth.py', 'logout')];
        expect(card.resolved).toBe(false);
        if (card.resolved) throw new Error('expected dead ref');
        expect(card.target).toBe('auth.py#logout');
    });

    it('keeps an unrealized placeholder ref with count suppressed', () => {
        const card = cards.byRef[refKey('export.py', 'run')];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.ownerFeatureId).toBe('f-3');
        expect(card.unrealized).toBe(true);
        expect(card.bindingCount).toBe(0);
    });
});

describe('buildHoverCards — byFeature', () => {
    const cards = buildHoverCards(REGISTRY, SIDECAR, fid => DESC[fid] ?? null);

    it('produces a card per feature keyed by id, with the threaded gist', () => {
        const card = cards.byFeature['f-1'];
        expect(card).toBeTruthy();
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.title).toBe('Auth');
        expect(card.gist).toBe('Handles authentication.');
        expect(card.bindingCount).toBe(1);
    });

    it('a binding-less unrealized feature gets a direct unrealized card', () => {
        const card = cards.byFeature['f-3'];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.ownerFeatureId).toBe('f-3');
        expect(card.unrealized).toBe(true);
        expect(card.bindingCount).toBe(0);
        // empty description → no gist.
        expect(card.gist).toBeNull();
    });
});

describe('buildHoverCards — graceful with no registry', () => {
    it('still builds feature cards from the sidecar when registry is null', () => {
        const cards = buildHoverCards(null, SIDECAR, fid => DESC[fid] ?? null);
        expect(cards.byRef).toEqual({}); // no registry refs to key
        const card = cards.byFeature['f-2'];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.title).toBe('Login form');
        expect(card.gist).toBe('Renders the login form.');
    });
});

// ── gist prefers the sidecar pitch (Fix 1) ────────────────────────────────────
// A SIDECAR variant carrying the Python-derived pitch on each feature.
const PITCHED_SIDECAR: SidecarData = {
    ...SIDECAR,
    features: {
        'f-1': { title: 'Auth', parent_id: null, realized: true, pitch: 'Authentication, sessions, and tokens.' },
        'f-2': { title: 'Login form', parent_id: 'f-1', realized: true, pitch: 'The login form widget.' },
        'f-3': { title: 'Planned export', parent_id: null, realized: false, pitch: 'Planned CSV export.' },
    },
} as SidecarData;

describe('buildHoverCards — gist equals the sidecar pitch when present', () => {
    const cards = buildHoverCards(REGISTRY, PITCHED_SIDECAR, fid => DESC[fid] ?? null);

    it('a ref card shows the owner pitch, not a firstSentence(description)', () => {
        const card = cards.byRef[refKey('auth.py', 'login')];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.ownerFeatureId).toBe('f-1');
        // Pitch wins over the threaded description's first sentence ("Handles
        // authentication.").
        expect(card.gist).toBe('Authentication, sessions, and tokens.');
    });

    it('a feature card shows the feature pitch', () => {
        const card = cards.byFeature['f-1'];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.gist).toBe('Authentication, sessions, and tokens.');
    });

    it('a binding-less unrealized feature card shows its pitch', () => {
        const card = cards.byFeature['f-3'];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.unrealized).toBe(true);
        expect(card.gist).toBe('Planned CSV export.');
    });
});

// A cross-feature ref: feature A (f-author) AUTHORS a citation pointing at a symbol
// OWNED by feature B (f-owner). The card's title/gist must be B's, never A's.
describe('buildHoverCards — cross-feature ref uses the OWNER, not the author', () => {
    const X_REGISTRY: RegistryData = {
        version: 1,
        features: {
            'f-author': { title: 'Author feature', parent_id: null },
            'f-owner': { title: 'Owner feature', parent_id: null },
        },
        bindings: [
            // The cited symbol is OWNED by f-owner.
            { file: 'pay.py', symbol_path: 'pay.py::charge', feature_id: 'f-owner' },
        ],
        refs: [
            // …but the ref is AUTHORED by f-author (records f-author as feature_id).
            { feature_id: 'f-author', label: 'charge', file: 'pay.py', symbol: 'charge', resolved: true },
        ],
    };
    const X_SIDECAR: SidecarData = {
        version: 5,
        by_feature: {
            'f-author': [],
            'f-owner': [{ file: 'pay.py', symbol: 'pay.py::charge' }],
        },
        by_file: {
            'pay.py': [{ symbol: 'pay.py::charge', feature_id: 'f-owner', feature_title: 'Owner feature' }],
        },
        features: {
            'f-author': { title: 'Author feature', parent_id: null, realized: true, pitch: 'AUTHOR pitch (wrong).' },
            'f-owner': { title: 'Owner feature', parent_id: null, realized: true, pitch: 'OWNER pitch (correct).' },
        },
    } as SidecarData;
    const X_DESC: Record<string, string> = {
        'f-author': 'Author description should NOT appear.',
        'f-owner': 'Owner description.',
    };

    it('the ref card carries the OWNER title + OWNER pitch', () => {
        const cards = buildHoverCards(X_REGISTRY, X_SIDECAR, fid => X_DESC[fid] ?? null);
        const card = cards.byRef[refKey('pay.py', 'charge')];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.ownerFeatureId).toBe('f-owner');
        expect(card.title).toBe('Owner feature');
        // The OWNER's pitch, never the authoring feature's.
        expect(card.gist).toBe('OWNER pitch (correct).');
        expect(card.gist).not.toBe('AUTHOR pitch (wrong).');
    });

    it('cross-feature owner is used even with no pitch (description fallback by owner)', () => {
        const noPitch: SidecarData = {
            ...X_SIDECAR,
            features: {
                'f-author': { title: 'Author feature', parent_id: null, realized: true },
                'f-owner': { title: 'Owner feature', parent_id: null, realized: true },
            },
        };
        const cards = buildHoverCards(X_REGISTRY, noPitch, fid => X_DESC[fid] ?? null);
        const card = cards.byRef[refKey('pay.py', 'charge')];
        if (!card.resolved || card.kind !== 'feature') throw new Error('expected feature card');
        expect(card.ownerFeatureId).toBe('f-owner');
        // No pitch → falls back to the OWNER's threaded description, not the author's.
        expect(card.gist).toBe('Owner description.');
    });
});
