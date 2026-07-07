/**
 * block-slash-menu.ts — author a typed-media block by typing `/` (U5).
 *
 * Typing `/` on an empty line under a feature opens a filtered menu (Diagram /
 * Image / Formula / Link). Choosing one inserts a block on the current feature.
 * Insertion reuses the existing block-edit channel: the menu posts a `block-edit`
 * with `action:'add'` (the host's `handleBlockEdit` → `appendBlockEdit` → Loop B's
 * `lower`-dispatch step persists it; a diagram's content is then filled by the
 * deterministic `lift` from the dependency graph on the next Loop A pass).
 *
 * The pure pieces (the kind catalog + the fuzzy filter) are unit-tested; the
 * Suggestion-plugin wiring (popup render, keyboard) is verified live in VS Code.
 */
import { fuzzyMatch, type FuzzyResult } from '../palette';

/** A typed-media kind offered by the slash menu. `glyph` is a Phosphor icon name
 *  (rendered via icons.ts) matching the block's lifecycle/medium. */
export interface BlockKindItem {
    kind: string;       // the BlockPlugin key (matches codoc/blocks/*)
    label: string;      // menu label
    hint: string;       // one-line description
    glyph: string;      // icons.ts name
}

/** The catalog — mirrors the registered reference plugins (diagram/image/latex/url/pdf). */
export const BLOCK_KINDS: BlockKindItem[] = [
    { kind: 'diagram', label: 'Diagram', hint: 'dependency graph of this feature', glyph: 'diamond' },
    { kind: 'image', label: 'Image', hint: 'a screenshot or mockup', glyph: 'square' },
    { kind: 'latex', label: 'Formula', hint: 'a LaTeX formula', glyph: 'function' },
    { kind: 'url', label: 'Link', hint: 'a reference URL the agent consults', glyph: 'link' },
    { kind: 'pdf', label: 'PDF', hint: 'a reference document the agent consults', glyph: 'file' },
];

/** Filter + rank the block kinds for a slash query (after the `/`). Empty query →
 *  the full catalog in declaration order. Otherwise fuzzy-match the label, best
 *  first. Pure — unit-tested, mirrors the ⌘K palette's ranking. */
export function filterBlockKinds(query: string): BlockKindItem[] {
    const q = query.trim();
    if (!q) return BLOCK_KINDS.slice();
    const scored: { item: BlockKindItem; res: FuzzyResult }[] = [];
    for (const item of BLOCK_KINDS) {
        const res = fuzzyMatch(q, item.label) ?? fuzzyMatch(q, item.kind);
        if (res) scored.push({ item, res });
    }
    scored.sort((a, b) => b.res.score - a.res.score);
    return scored.map(s => s.item);
}

/** Mint a client-side block id (`blk-…`) matching codoc/model/ids.py:new_block_id's
 *  prefix, so a slash-inserted block has a stable id the store upserts on `add`. */
export function mintBlockId(): string {
    const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
    if (c?.randomUUID) return 'blk-' + c.randomUUID().replace(/-/g, '').slice(0, 12);
    return 'blk-' + Math.abs(Date.now() ^ Math.floor(Math.random() * 1e9)).toString(36);
}

/** The block-edit message a slash selection emits (reuses the existing channel). */
export interface BlockCreateMsg {
    block_id: string;
    feature_id: string;
    kind: string;
    action: 'add';
    content: string;
}

/** Build the `add` block-edit for a chosen kind on a feature. Image/url carry the
 *  provided ref as initial content; diagram/latex start empty (diagram is filled by
 *  `lift`, latex/formula by the author). Pure — testable. */
export function buildBlockCreate(kind: string, featureId: string, ref = ''): BlockCreateMsg {
    return { block_id: mintBlockId(), feature_id: featureId, kind, action: 'add', content: ref };
}
