/**
 * guards.ts: two plugins that keep the page from disagreeing with the shadows.
 *
 * The recorder can only speak for text inside blocks it knows. `structureGuard`
 * therefore refuses any transaction that adds, drops, duplicates or un-ids a
 * top-level block unless a gesture recorded it.
 * `nfcGuard` composes text a transaction inserted in decomposed form, because
 * the store and the shadows count NFC characters and the page has to count
 * the same.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { Plugin, type EditorState, type Transaction } from '@tiptap/pm/state';

import { nfc } from '../calc/projection';
import { RECORDED } from './commands';

/** The document's block identities, in order. */
function structure(doc: PMNode): string {
    const ids: string[] = [];
    doc.forEach(b => ids.push(`${b.type.name}:${b.attrs.bid ?? '?'}`));
    return ids.join('|');
}

/** Whether two documents hold the same blocks, of the same types, in the same order. */
export function sameStructure(before: PMNode, after: PMNode): boolean {
    return structure(before) === structure(after);
}

export function structureGuard(): Plugin {
    return new Plugin({ filterTransaction: (tr, st) => !!tr.getMeta(RECORDED) || !tr.docChanged || sameStructure(st.doc, tr.doc) });
}

/**
 * Compose any decomposed run these transactions inserted, and record nothing:
 * the op that carried the text already said NFC.
 *
 * Only what was inserted is composed, one inserted run at a time, which is the
 * unit the shadow and the store use. `e` and U+0301 typed as two keystrokes
 * are two characters everywhere, so composing the text node they landed in
 * would shorten a paragraph by a character no op removed. Without this, a
 * decomposed keystroke leaves the page one scalar longer than the shadow and
 * every anchor after it is off by one.
 */
function composeNFC(trs: readonly Transaction[], _old: EditorState, next: EditorState): Transaction | null {
    const ranges: Array<[number, number]> = [];
    trs.forEach((t, i) => {
        t.steps.forEach((step, j) => {
            step.getMap().forEach((_a, _b, fromB, toB) => {
                let from = fromB, to = toB;
                const rest = t.mapping.slice(j + 1);
                from = rest.map(from, -1);
                to = rest.map(to, 1);
                for (const later of trs.slice(i + 1)) { from = later.mapping.map(from, -1); to = later.mapping.map(to, 1); }
                if (to > from) ranges.push([from, to]);
            });
        });
    });
    if (!ranges.length) return null;
    const size = next.doc.content.size;
    const fixes: Array<{ from: number; to: number; node: PMNode }> = [];
    for (const [from, to] of ranges) {
        const a = Math.max(0, from), b = Math.min(size, to);
        if (b <= a) continue;
        const $a = next.doc.resolve(a), $b = next.doc.resolve(b);
        if (!$a.parent.isTextblock || $a.parent !== $b.parent) continue;   // not one run of text
        const raw = next.doc.textBetween(a, b);
        const composed = nfc(raw);
        if (composed === raw) continue;
        const marks = next.doc.nodeAt(a)?.marks ?? [];
        fixes.push({ from: a, to: b, node: next.schema.text(composed, marks) });
    }
    if (!fixes.length) return null;
    let tr = next.tr;
    for (const f of fixes.sort((x, y) => y.from - x.from)) tr = tr.replaceWith(f.from, f.to, f.node);
    return tr.setMeta(RECORDED, []).setMeta('addToHistory', false);
}

export function nfcGuard(): Plugin {
    return new Plugin({ appendTransaction: composeNFC });
}
