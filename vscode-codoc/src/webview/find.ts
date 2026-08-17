/**
 * find.ts — the pure logic of in-document find & replace (⌘F / ⌘⌥F).
 *
 * Why the tree editor needs its own: `tree.codoc` is a READ-ONLY derived export
 * that the daemon overwrites, so the raw text editor — where VS Code's Find
 * widget would work — was never a place you could actually change anything. And
 * a webview gets no Find widget at all: ⌘F reaches the iframe as a plain keydown
 * and nothing catches it. So the document that IS editable had no search, which
 * is why "where does the tree say X" meant reading the whole tree.
 *
 * DOM-free and ProseMirror-free on purpose. It works over SearchBlocks — one per
 * heading and per paragraph, each carrying the document position its text starts
 * at — so the caller can map a hit straight back to a document range, and every
 * rule here (matching, ordering, case preservation) is unit-testable without an
 * editor.
 */

export interface FindOptions {
    caseSensitive: boolean;
    wholeWord: boolean;
    regex: boolean;
}

export const DEFAULT_FIND_OPTIONS: FindOptions = {
    caseSensitive: false,
    wholeWord: false,
    regex: false,
};

/** One searchable block: a feature's title, or one paragraph of its description.
 *  `base` is the document position of the block's FIRST character, so char offset
 *  `i` maps to `base + i` (the display-space contract in display-text.ts). */
export interface SearchBlock {
    text: string;
    base: number;
    fid: string;
    field: 'title' | 'description';
}

export interface FindMatch {
    /** Document range of the hit. */
    from: number;
    to: number;
    /** The matched text, verbatim — what preserve-case reads and replace consumes. */
    text: string;
    fid: string;
    field: 'title' | 'description';
    /** Regex capture groups, for `$1` in a replacement. Empty in literal mode. */
    groups: string[];
}

const ESCAPE_RE = /[.*+?^${}()|[\]\\]/g;

/**
 * Compile the query, or null when it cannot be searched (empty, or an invalid
 * pattern while regex mode is on — an unfinished `(` is what half a typed regex
 * looks like, and it must read as "no matches yet", never as an exception).
 *
 * Whole-word uses lookarounds rather than `\b` so it behaves the same either
 * side of a non-Latin script; `\b` is defined against ASCII word chars, and this
 * tree may be authored in any language.
 */
export function buildMatcher(query: string, opts: FindOptions): RegExp | null {
    if (!query) return null;
    let source = opts.regex ? query : query.replace(ESCAPE_RE, '\\$&');
    if (opts.wholeWord) source = `(?<![\\p{L}\\p{N}_])(?:${source})(?![\\p{L}\\p{N}_])`;
    const flags = `gu${opts.caseSensitive ? '' : 'i'}`;
    try {
        return new RegExp(source, flags);
    } catch {
        return null;
    }
}

/**
 * Every match across `blocks`, in document order.
 *
 * A zero-length match (`a*`, `^`) advances the cursor by one rather than
 * looping — an empty pattern is a legitimate thing to type on the way to a real
 * one, and hanging the editor over it is not an acceptable intermediate state.
 */
export function findInBlocks(blocks: SearchBlock[], query: string, opts: FindOptions): FindMatch[] {
    const re = buildMatcher(query, opts);
    if (!re) return [];
    const out: FindMatch[] = [];
    for (const block of blocks) {
        re.lastIndex = 0;
        for (let m = re.exec(block.text); m; m = re.exec(block.text)) {
            if (m[0].length === 0) {
                re.lastIndex++;
                continue;
            }
            out.push({
                from: block.base + m.index,
                to: block.base + m.index + m[0].length,
                text: m[0],
                fid: block.fid,
                field: block.field,
                groups: m.slice(1).map(g => g ?? ''),
            });
            if (out.length >= MAX_MATCHES) return out;
        }
    }
    return out;
}

/** A ceiling on how many hits are tracked at once. Past this the count stops
 *  being read as a count, and every one is a live decoration. */
export const MAX_MATCHES = 2000;

/**
 * The match to land on when stepping from document position `pos`.
 *
 * Forward: the first match starting at or after `pos`. Backward: the last one
 * starting strictly before it. Both wrap. Returns -1 for no matches.
 *
 * `pos` is where the caret is, so "find next" from inside a hit moves off it
 * rather than re-selecting it — the behaviour every editor's ⌘G has.
 */
export function stepIndexFrom(matches: FindMatch[], pos: number, forward: boolean): number {
    if (!matches.length) return -1;
    if (forward) {
        const i = matches.findIndex(m => m.from >= pos);
        return i === -1 ? 0 : i;
    }
    for (let i = matches.length - 1; i >= 0; i--) {
        if (matches[i].from < pos) return i;
    }
    return matches.length - 1;
}

/** Move `index` by one, wrapping. */
export function wrapIndex(index: number, count: number, delta: number): number {
    if (count <= 0) return -1;
    return ((index + delta) % count + count) % count;
}

/**
 * The text a match is replaced with.
 *
 * In regex mode `$1`…`$9` and `$&` expand from the captures. With
 * `preserveCase`, a replacement inherits the SHAPE of what it replaced — ALL
 * CAPS stays all caps, Capitalized stays capitalized — which is what makes
 * replace-all across prose survive sentence starts without a second pass by hand.
 */
export function replacementFor(
    match: FindMatch, replacement: string, opts: FindOptions & { preserveCase?: boolean },
): string {
    let out = replacement;
    if (opts.regex) {
        out = out.replace(/\$([1-9]|&)/g, (_all, key: string) =>
            key === '&' ? match.text : (match.groups[Number(key) - 1] ?? ''));
    }
    if (!opts.preserveCase) return out;
    return matchCase(match.text, out);
}

/** Give `replacement` the case shape of `source`. Mixed case is left alone —
 *  guessing at "camelCase" would mangle more than it fixed. */
export function matchCase(source: string, replacement: string): string {
    const letters = source.replace(/[^\p{L}]/gu, '');
    if (!letters) return replacement;
    if (letters === letters.toUpperCase() && letters !== letters.toLowerCase()) {
        return replacement.toUpperCase();
    }
    if (source[0] === source[0].toUpperCase() && source.slice(1) === source.slice(1).toLowerCase()) {
        return replacement.charAt(0).toUpperCase() + replacement.slice(1);
    }
    return replacement;
}

/** What the widget renders from after every search / step / replace. */
export interface FindState {
    count: number;
    /** Index of the current match, or -1 when there is none. */
    index: number;
    query: string;
}

/** "3 of 17", or "No results" — the one string the widget shows for state. */
export function matchLabel(index: number, count: number): string {
    if (count <= 0) return 'No results';
    const shown = index < 0 ? 1 : index + 1;
    return `${shown} of ${count}${count >= MAX_MATCHES ? '+' : ''}`;
}
