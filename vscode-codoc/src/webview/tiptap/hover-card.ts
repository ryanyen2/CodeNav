/**
 * hover-card.ts — the tier-1 hover-preview card in the webview (U4).
 *
 * The reading-surface twin of the raw-text `providers/hover.ts` card: hovering a
 * `codeRef` chip or a feature dependency link shows a compact card — the owning
 * feature's title, a one-line gist (or a muted "No description yet"), and an
 * "N refs" / "plan" marker — built from the SAME `ResolvedCard` contract
 * (registry-model.ts) that the raw-text hover renders, so the two surfaces agree.
 *
 * Strict invariant: this is a transient DOM overlay only. It is NEVER serialized
 * into the doc and never dispatches a doc transaction — `renderTreeFromDoc` stays
 * byte-identical. The card data is precomputed host-side (the webview can't read
 * files or call Python) and shipped in `DocPayload.hoverCards`; here we only look
 * it up by the hovered chip/link's key and render it.
 *
 * Geometry + single-open + outside-click/Escape dismissal are reused verbatim from
 * `comment-decorations.ts:showCard` (extracted there for exactly this), so the
 * comment popover and the hover card never both stay visible.
 */
import type { ResolvedCard } from '../../state/registry-model';
import { refKey } from '../../state/registry-model';
import { showCard, isOpenCard } from './comment-decorations';

function elc(tag: string, cls?: string, text?: string): HTMLElement {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

/* ── pure card model (DOM-free, unit-testable) ────────────────────────────────
 *  cardModel() distills a ResolvedCard into the exact display fields the card
 *  renders — title, gist (with the "No description yet" fallback baked in), the
 *  meta string ("N refs" / "plan"), the dead-ref target/note, and the file-owner
 *  list. buildCardDom() is then a thin DOM projection over this model, so all the
 *  branching logic (gist fallback, count suppression, owner enumeration, dead-ref
 *  state) is tested under the `node` environment without a DOM.                  */
export interface CardModel {
    state: 'feature' | 'file' | 'dead';
    title: string;
    /** Rendered gist line; the "No description yet" fallback is already applied. */
    gist: string;
    /** True when the gist is the muted fallback (not a real description). */
    gistMuted: boolean;
    /** The "N refs" / "plan" / "used by N features" meta line. */
    meta: string;
    /** Unrealized placeholder → the count is suppressed in favour of the plan marker. */
    plan: boolean;
    /** File-owner titles (file card only). */
    owners: string[];
    /** Broken `file#symbol` (dead card only). */
    target: string;
    note: string;
    /** The navigate-button label, or null when the card offers no navigate. */
    openLabel: string | null;
}

const NO_DESC = 'No description yet';
const DEAD_NOTE = 'The linked code can’t be found — flagged in the Connections panel.';

export function cardModel(card: ResolvedCard): CardModel {
    if (!card.resolved) {
        return {
            state: 'dead', title: 'Unresolved reference', gist: '', gistMuted: false,
            meta: '', plan: false, owners: [], target: card.target, note: DEAD_NOTE,
            openLabel: null,
        };
    }
    if (card.kind === 'file') {
        const n = card.owners.length;
        return {
            state: 'file', title: card.file,
            gist: n === 0 ? 'No owning features yet' : '', gistMuted: n === 0,
            meta: `used by ${n} feature${n === 1 ? '' : 's'}`, plan: false,
            owners: card.owners.map(o => o.title), target: '', note: '',
            openLabel: 'Open file',
        };
    }
    return {
        state: 'feature', title: card.title,
        gist: card.gist ?? NO_DESC, gistMuted: !card.gist,
        // count suppressed for an unrealized placeholder (shape = kind, not a colour)
        meta: card.unrealized ? '◇ plan' : `${card.bindingCount} ref${card.bindingCount === 1 ? '' : 's'}`,
        plan: card.unrealized, owners: [], target: '', note: '',
        openLabel: 'Open code',
    };
}

/**
 * Thin DOM projection of `cardModel`. No editor state, no events beyond the
 * optional navigate handler. The display logic is fully in `cardModel` (tested);
 * this only places the fields into elements.
 *
 *   - feature card → title + gist (or muted "No description yet") + an "N refs"
 *     meta, or a "plan" marker when the owning feature is an unrealized
 *     placeholder (count suppressed — shape = kind, not a new colour);
 *   - file card    → "used by N features" + the owning-feature list;
 *   - dead ref     → an explicit "Unresolved reference" state with the broken
 *     target (no card content), pointing at the Connections panel.
 *
 * `onOpen` (optional) wires the tier-2 navigate affordance; omitted in tests.
 */
export function buildCardDom(card: ResolvedCard, onOpen?: () => void): HTMLElement {
    const m = cardModel(card);
    const pop = elc('div', 'ce-hovercard');

    if (m.state === 'dead') {
        pop.classList.add('dead');
        const head = elc('div', 'ce-hc-head');
        head.append(elc('span', 'ce-hc-warn', '⚠'), elc('span', 'ce-hc-title', m.title));
        pop.append(head);
        pop.append(elc('div', 'ce-hc-target', m.target));
        pop.append(elc('div', 'ce-hc-note', m.note));
        return pop;
    }

    pop.append(elc('div', 'ce-hc-title', m.title));
    if (m.state === 'file') {
        pop.append(elc('div', 'ce-hc-meta', m.meta));
        if (m.owners.length === 0) {
            pop.append(elc('div', 'ce-hc-gist muted', m.gist));
        } else {
            const list = elc('div', 'ce-hc-owners');
            for (const t of m.owners) list.append(elc('div', 'ce-hc-owner', t));
            pop.append(list);
        }
    } else {
        pop.append(elc('div', 'ce-hc-gist' + (m.gistMuted ? ' muted' : ''), m.gist));
        const meta = elc('div', 'ce-hc-meta');
        meta.append(elc('span', m.plan ? 'ce-hc-plan' : undefined, m.meta));
        pop.append(meta);
    }
    if (onOpen && m.openLabel) pop.append(openButton(onOpen, m.openLabel));
    return pop;
}

function openButton(onOpen: () => void, label: string): HTMLElement {
    const b = elc('button', 'ce-hc-open', label);
    b.addEventListener('mousedown', ev => ev.preventDefault());
    b.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); onOpen(); });
    return b;
}

const HOVER_DELAY_MS = 350; // mirror the comment-icon hover delay

export interface HoverCardData {
    byRef: Record<string, ResolvedCard>;
    byFeature: Record<string, ResolvedCard>;
}

export interface HoverCardHandlers {
    /** The latest precomputed cards (registry/sidecar are host-side). */
    getCards: () => HoverCardData | null;
    /** Tier-2 navigate: open the code/binding for a resolved code ref. */
    onOpenBinding: (file: string, symbol: string) => void;
    /** Tier-2 navigate: go to a feature (a feature-link card). */
    onNavigate: (fid: string) => void;
}

/** What the hovered element resolves to: a code ref (chip) or a feature link. */
type HoverTarget =
    | { kind: 'ref'; file: string; symbol: string }
    | { kind: 'feature'; fid: string };

/** Classify a hovered element as a card anchor, or null if it isn't one. */
function targetFor(el: HTMLElement): { anchor: HTMLElement; target: HoverTarget } | null {
    const chip = el.closest('.codoc-code-ref') as HTMLElement | null;
    if (chip) {
        return {
            anchor: chip,
            target: { kind: 'ref', file: chip.getAttribute('data-file') || '', symbol: chip.getAttribute('data-symbol') || '' },
        };
    }
    const link = el.closest('.ce-thread[data-fid]') as HTMLElement | null;
    if (link) {
        return { anchor: link, target: { kind: 'feature', fid: link.dataset.fid || '' } };
    }
    return null;
}

function cardFor(data: HoverCardData, t: HoverTarget): ResolvedCard | null {
    if (t.kind === 'ref') return data.byRef[refKey(t.file, t.symbol || null)] ?? null;
    return data.byFeature[t.fid] ?? null;
}

/**
 * Wire hover-card behavior onto an editor DOM root. Returns a disposer.
 *
 * Dismissal: the card opens after a ~350ms hover and stays alive while the pointer
 * is over the chip OR the card; it dismisses only after the pointer leaves BOTH
 * (the card's own mouseleave, registered by showCard, plus the anchor mouseleave
 * delay here). Only one card is visible at a time (showCard enforces it across the
 * comment popover too). Keyboard: a focused chip opens the card pinned on
 * Enter/Space; Escape dismisses (showCard's pinned listeners). Reduced-motion is
 * gated by CSS (`body.vscode-reduce-motion` removes the reveal animation).
 */
export function attachHoverCards(root: HTMLElement, handlers: HoverCardHandlers): () => void {
    let hoverTimer = 0;
    let openEl: HTMLElement | null = null;
    let closeOpen: (() => void) | null = null;

    function dismiss(): void {
        if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
        if (openEl && isOpenCard(openEl)) closeOpen?.();
        openEl = null;
        closeOpen = null;
    }

    function open(anchor: HTMLElement, card: ResolvedCard, pinned: boolean): void {
        const onOpen = card.resolved
            ? (card.kind === 'file'
                ? (): void => handlers.onOpenBinding(card.file, '')
                : (): void => handlers.onNavigate(card.ownerFeatureId))
            : undefined;
        const content = buildCardDom(card, onOpen);
        openEl = content;
        closeOpen = showCard(anchor, content, { pinned });
    }

    function onOver(ev: Event): void {
        const found = targetFor(ev.target as HTMLElement);
        if (!found) return;
        const data = handlers.getCards();
        if (!data) return;
        const card = cardFor(data, found.target);
        if (!card) return;
        if (hoverTimer) clearTimeout(hoverTimer);
        hoverTimer = window.setTimeout(() => { open(found.anchor, card, false); }, HOVER_DELAY_MS);
    }

    function onOut(ev: Event): void {
        // Leaving a chip/link: cancel a pending open; let the card's own mouseleave
        // (showCard) dismiss it if the pointer didn't move onto the card. A short
        // delay covers the chip→card gap (the card may not be :hover yet).
        const e = ev as MouseEvent;
        const found = targetFor(e.target as HTMLElement);
        if (!found) return;
        if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
        window.setTimeout(() => {
            if (openEl && isOpenCard(openEl) && !openEl.matches(':hover')) dismiss();
        }, 120);
    }

    function onKey(ev: KeyboardEvent): void {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        const found = targetFor(ev.target as HTMLElement);
        if (!found) return;
        const data = handlers.getCards();
        if (!data) return;
        const card = cardFor(data, found.target);
        if (!card) return;
        ev.preventDefault();
        open(found.anchor, card, true); // pinned: Escape / outside-click dismiss
    }

    root.addEventListener('mouseover', onOver);
    root.addEventListener('mouseout', onOut);
    root.addEventListener('keydown', onKey);

    return () => {
        root.removeEventListener('mouseover', onOver);
        root.removeEventListener('mouseout', onOut);
        root.removeEventListener('keydown', onKey);
        dismiss();
    };
}
