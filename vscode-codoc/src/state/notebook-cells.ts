/**
 * Which cell a notebook citation names.
 *
 * A description can now cite a step of a notebook — `[one fold](codoc:work/churn.ipynb#train)`
 * — because a notebook's markdown headings name its sections and those sections are
 * indexed chunks (`codoc/lang/notebook.py`). Clicking one has to land on the cell, and
 * every path `openRef` already had makes it WORSE than a no-op: a `.ipynb` opened as a
 * text document is a wall of JSON, the document-symbol provider has nothing to say about
 * it, and the fallback regex happily matches `"name":` inside a base64 output and
 * reveals a line of PNG. The reader is then looking at machine noise where a sentence
 * promised them the code, which is the version of a broken citation that costs the most
 * trust.
 *
 * So the notebook branch owns the whole open (`openNotebookDocument`, not
 * `openTextDocument`) and this module answers only the grammar question. It reads the
 * cells of the OPEN document rather than the file's bytes, so a citation still resolves
 * against a cell the author has edited and not yet saved — the case where a reader is
 * most likely to be clicking.
 *
 * The rules mirror `codoc/lang/notebook.py`: a heading names the statement run under it,
 * `_slug` folds it to word characters (so `## 加载数据` addresses as plainly as
 * `## Load the data`), a repeated `## Train` becomes `train[1]`, and sections are FLAT
 * because headings are a reading order and not a namespace. A dotted address is a
 * MEMBER of its section (`train.Model.fit`), which is why this cannot use `symbolLeaf`'s
 * rule alone: the leaf names the declaration to find, but only the prefix says which
 * section to look in, and two sections may each declare a `fit`.
 */

/** One cell, reduced to what the grammar reads. `markup` is VS Code's name for markdown. */
export interface NotebookCellText {
    kind: 'code' | 'markup';
    text: string;
}

/** Where a citation lands: the cell, and the 0-based line inside it (-1 for the cell itself). */
export interface NotebookRef {
    cell: number;
    line: number;
}

/** Mirrors `_HEADING` in `codoc/lang/notebook.py`. */
const HEADING = /^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;

/** True for a file the notebook grammar claims. */
export function isNotebook(file: string): boolean {
    return /\.ipynb$/i.test(file);
}

/** A heading as an address, mirroring `_slug`: word characters, lowercased, dash-joined.
 *
 *  Dots become spaces first for the reason the Python does it — a dot in a symbol path
 *  means "owned by", so `## Step 1.2` must not address as a member of `step 1`. */
function slug(title: string): string {
    const cleaned = title.replace(/\./g, ' ').replace(/[^\p{L}\p{N}_]+/gu, '-');
    return cleaned.replace(/^[-_]+|[-_]+$/g, '').toLowerCase();
}

/** `name`, or the next free `name[n]` — the chunker's `_uniquify` convention. */
function uniquify(name: string, seen: Set<string>): string {
    if (!seen.has(name)) {
        seen.add(name);
        return name;
    }
    let index = 1;
    while (seen.has(`${name}[${index}]`)) index += 1;
    seen.add(`${name}[${index}]`);
    return `${name}[${index}]`;
}

interface Section {
    /** The address the chunker gave it, or null for the run before the first heading. */
    name: string | null;
    /** Where its heading is written (the unnamed run has none). */
    head: NotebookRef | null;
    /** Indexes of the cells that belong to it, in order. */
    cells: number[];
}

/** The notebook split at its headings, in reading order.
 *
 *  A heading part-way down a markdown cell starts a section there, so a section's first
 *  cell can be one it shares with the section above — which is why a cell index appears
 *  in two sections rather than being partitioned. The unnamed run comes first and is what
 *  makes a heading-less notebook read as a script. */
export function notebookSections(cells: NotebookCellText[]): Section[] {
    const seen = new Set<string>();
    const sections: Section[] = [{ name: null, head: null, cells: [] }];
    cells.forEach((cell, index) => {
        if (cell.kind === 'code') {
            sections[sections.length - 1].cells.push(index);
            return;
        }
        let opened = false;
        cell.text.split('\n').forEach((line, lineIndex) => {
            const heading = HEADING.exec(line);
            if (!heading) return;
            sections.push({
                name: uniquify(slug(heading[2]) || 'section', seen),
                head: { cell: index, line: lineIndex },
                cells: [index],
            });
            opened = true;
        });
        if (!opened) sections[sections.length - 1].cells.push(index);
    });
    return sections;
}

/** The line inside `text` that declares `leaf`, or -1.
 *
 *  Declarations only — `def`, `class`, or an assignment at the head of a line — because
 *  the point of a citation is the place a name is GIVEN its meaning, and a name is used
 *  far more often than it is declared. */
function declarationLine(text: string, leaf: string): number {
    const escaped = leaf.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const declares = new RegExp(
        `^\\s*(?:async\\s+)?(?:def|class)\\s+${escaped}\\b` +
        `|^\\s*${escaped}\\s*(?::[^=]+)?=(?!=)` +
        `|^\\s*${escaped}\\s*:\\s*\\S`,
    );
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i += 1) {
        if (declares.test(lines[i])) return i;
    }
    return -1;
}

/**
 * Where the citation `symbol` lands in `cells`, or null.
 *
 * `symbol` may arrive qualified (`work/churn.ipynb::train.Model`) or bare, because both
 * are authored. Resolution is by longest section prefix rather than by splitting on the
 * first dot: a section slug contains dashes and can itself contain nothing else, but its
 * MEMBER path is dotted (`train.Model.fit`), so the split has to be made where a section
 * name actually ends.
 *
 * Null rather than a guess. Revealing an arbitrary cell says something the notebook does
 * not, and the reader cannot tell that from a citation that worked.
 */
export function notebookCellForRef(cells: NotebookCellText[], symbol: string): NotebookRef | null {
    const path = symbol.includes('::') ? symbol.slice(symbol.indexOf('::') + 2) : symbol;
    if (!path) return null;
    const sections = notebookSections(cells);

    // The section itself: its heading is the line that says which section this is, so the
    // reveal lands on `## Train` rather than on the first statement under it.
    const named = sections.find(s => s.name === path);
    if (named?.head) return named.head;

    // `__module__` is the glue above the first heading, which is the top of the file —
    // and in a notebook that means the first cell that holds code, not cell zero, which
    // is nearly always the title.
    const owner = sections.find(s => path.startsWith(`${s.name}.`)) ?? sections[0];
    const local = owner.name ? path.slice(owner.name.length + 1) : path;
    const leaf = local.includes('.') ? local.slice(local.lastIndexOf('.') + 1) : local;

    if (leaf === '__module__') {
        const first = owner.cells.find(i => cells[i].kind === 'code');
        if (first === undefined) return owner.head;
        return { cell: first, line: 0 };
    }

    for (const index of owner.cells) {
        if (cells[index].kind !== 'code') continue;
        const line = declarationLine(cells[index].text, leaf);
        if (line >= 0) return { cell: index, line };
    }
    // A section whose member cannot be found is still worth landing on: the citation
    // named a step that exists, and the heading is the honest answer to where it is.
    return owner.head;
}
