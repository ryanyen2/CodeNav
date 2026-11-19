/**
 * symbol.ts: where in a file the symbol a citation names is.
 *
 * No `vscode` import, so vitest can load it.
 */

/**
 * The offset in `text` of the symbol that `symbol` (`file::symbol_path`) names:
 * its definition when a Python or JavaScript keyword introduces it, otherwise its
 * first occurrence. -1 when it does not occur, or when `symbol` names a whole file.
 *
 * The name is matched whole: the characters on either side must not be able to
 * continue an identifier. `\b` cannot say that for a name that starts or ends
 * with a non-word character, such as the private member `#run` or `$el`.
 */
export function symbolOffset(text: string, symbol: string): number {
    const at = symbol.indexOf('::');
    const leaf = at < 0 ? '' : symbol.slice(at + 2).split('.').pop();
    if (!leaf) return -1;
    const name = `(?<![\\w$#])${leaf.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w$])`;
    const m = new RegExp(`\\b(?:def|class|function|const|let|var|async function)\\s+${name}`).exec(text)
        ?? new RegExp(name).exec(text);
    return m ? m.index : -1;
}
