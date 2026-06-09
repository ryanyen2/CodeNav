/**
 * pm-doc.ts — the pure ProseMirror document model for the codoc rich editor.
 *
 * This is the authored-intent source of truth (persisted as `.codoc/tree.doc.json`).
 * It is *plain JSON* — no TipTap, no DOM, no `vscode` import — so the serializer,
 * deserializer, and round-trip tests run under vitest, and the host can reason
 * about the doc without a webview. The live TipTap extensions (U1b, webview only)
 * are constructed to match this exact node/mark vocabulary.
 *
 * Document shape (flat outliner — depth lives on the heading, mirroring how
 * `tree.codoc` encodes depth as indentation and how `doc-layout.ts` linearizes
 * the tree into one article):
 *
 *   doc
 *     ├─ featureHeading { fid, level, retired, realized }  → title inline runs
 *     ├─ paragraph                                         → a description paragraph
 *     ├─ paragraph                                         → (blank-line-separated)
 *     ├─ featureHeading { … }                              → next feature
 *     └─ …
 *
 * Paragraphs following a heading (until the next heading) are that feature's
 * description. `featureHeading.attrs.fid` is the rich analogue of the hidden
 * `⟨f-id⟩` marker; `null` means a newly authored heading whose id is minted by
 * the Python `apply_op` seam and stamped back on the store→doc rebuild — the
 * webview NEVER mints `f-` ids.
 */
export const NODE_DOC = 'doc';
export const NODE_FEATURE_HEADING = 'featureHeading';
export const NODE_PARAGRAPH = 'paragraph';
export const NODE_TEXT = 'text';
export const NODE_CODE_REF = 'codeRef';
export const NODE_HARD_BREAK = 'hardBreak';

export const MARK_STRONG = 'strong';
export const MARK_EM = 'em';
export const MARK_HIGHLIGHT = 'highlight';
export const MARK_COMMENT = 'comment';
export const MARK_AUTHOR = 'author';

/** Commitment mode — drives OPACITY (pen solid, pencil faded). */
export type AuthorMode = 'pen' | 'pencil';
/** Who authored a span — drives COLOR/tint. Open-ended on purpose (new agents). */
export type AuthorRole = 'human' | 'claude-code' | 'codex' | 'gemini' | 'cursor' | string;

/** The `author` mark: per-character provenance the plain-text projection can't carry. */
export interface AuthorMarkAttrs {
    authorId: string;
    role: AuthorRole;
    mode: AuthorMode;
    ts: number;
}

export interface PMMark {
    type: string;
    attrs?: Record<string, unknown>;
}

export interface PMNode {
    type: string;
    attrs?: Record<string, unknown>;
    content?: PMNode[];
    marks?: PMMark[];
    text?: string;
}

export interface FeatureHeadingAttrs {
    fid: string | null;
    level: number;
    retired: boolean;
    realized: boolean;
}

export interface CodeRefAttrs {
    /** Raw label text from `[label](…)` — kept verbatim (may be empty) for exact round-trip. */
    label: string;
    file: string;
    symbol: string | null;
}

// ── constructors ─────────────────────────────────────────────────────────────

export function textNode(text: string, marks?: PMMark[]): PMNode {
    return marks && marks.length ? { type: NODE_TEXT, text, marks } : { type: NODE_TEXT, text };
}

export function codeRefNode(attrs: CodeRefAttrs, marks?: PMMark[]): PMNode {
    const node: PMNode = { type: NODE_CODE_REF, attrs: { ...attrs } };
    if (marks && marks.length) node.marks = marks;
    return node;
}

export function paragraphNode(content: PMNode[]): PMNode {
    return { type: NODE_PARAGRAPH, content };
}

export function featureHeadingNode(attrs: FeatureHeadingAttrs, content: PMNode[]): PMNode {
    return { type: NODE_FEATURE_HEADING, attrs: { ...attrs }, content };
}

export function makeDoc(content: PMNode[]): PMNode {
    return { type: NODE_DOC, content };
}

export function emptyDoc(): PMNode {
    return makeDoc([]);
}

// ── inline ↔ text projection ─────────────────────────────────────────────────

/**
 * Inline citation regex — IDENTICAL to `parse.extract_refs` / `tree-model.extractRefs`
 * (kept as its own copy so this module imports nothing).  `[label](codoc:file#symbol)`
 * with an optional `#symbol`.
 */
const REF_RE_SOURCE = '\\[([^\\]]*)\\]\\(codoc:([^)#]+)(?:#([^)]+))?\\)';

/** Serialize one codeRef to its canonical `[label](codoc:file#symbol)` text. */
export function codeRefToText(attrs: CodeRefAttrs): string {
    const target = attrs.symbol ? `${attrs.file}#${attrs.symbol}` : attrs.file;
    return `[${attrs.label}](codoc:${target})`;
}

/**
 * Concatenate inline runs into their plain-text projection (what lands in
 * `tree.codoc`): text verbatim, codeRef → markdown link, hardBreak → "\n".
 * Marks (bold/italic/highlight/comment/author) are intentionally DROPPED — they
 * live only in `tree.doc.json`.
 */
export function inlineRunsToText(content: PMNode[] | undefined): string {
    let s = '';
    for (const n of content ?? []) {
        if (n.type === NODE_TEXT) s += n.text ?? '';
        else if (n.type === NODE_CODE_REF && n.attrs) s += codeRefToText(n.attrs as unknown as CodeRefAttrs);
        else if (n.type === NODE_HARD_BREAK) s += '\n';
    }
    return s;
}

/**
 * Split prose into inline runs at `[label](codoc:…)` boundaries — the inverse of
 * `inlineRunsToText`. Mirrors `doc-layout.weaveParagraph` but keeps the RAW label
 * (so an empty `[]` round-trips) and emits codeRef *nodes*. Empty text slices are
 * never emitted (ProseMirror forbids empty text nodes).
 */
export function textToInlineRuns(text: string): PMNode[] {
    const runs: PMNode[] = [];
    const re = new RegExp(REF_RE_SOURCE, 'g');
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
        if (m.index > last) runs.push(textNode(text.slice(last, m.index)));
        runs.push(codeRefNode({ label: m[1], file: m[2], symbol: m[3] ?? null }));
        last = m.index + m[0].length;
    }
    if (last < text.length) runs.push(textNode(text.slice(last)));
    return runs;
}

// ── description ↔ paragraph blocks (the per-section editor seam) ──────────────

/**
 * A feature's description string → paragraph blocks (split on blank lines, the
 * `weaveBlocks` contract). This is the content a section's TipTap editor mounts.
 * A blank description yields one empty paragraph (ProseMirror needs ≥1 block).
 */
export function descriptionToBlocks(description: string): PMNode[] {
    if (!description.trim()) return [paragraphNode([])];
    return description
        .split(/\n{2,}/)
        .map(p => paragraphNode(textToInlineRuns(p)));
}

/**
 * Paragraph blocks → description string (inverse of `descriptionToBlocks`).
 * Empty paragraphs are dropped; the rest join with a blank line. Marks (bold,
 * author, …) are projected away — only text + codeRef markdown survive, matching
 * what `tree.codoc` can carry.
 */
export function blocksToDescriptionText(blocks: PMNode[]): string {
    return blocks
        .filter(b => b.type === NODE_PARAGRAPH)
        .map(b => inlineRunsToText(b.content))
        .filter(s => s.trim().length > 0)
        .join('\n\n');
}

/** Pull the paragraph blocks that belong to feature `fid` out of a whole-tree doc
 *  (the paragraphs between that heading and the next). Returns [] if not found. */
export function descriptionBlocksForFid(doc: PMNode, fid: string): PMNode[] {
    const blocks = doc.content ?? [];
    const out: PMNode[] = [];
    let i = 0;
    while (i < blocks.length) {
        const b = blocks[i];
        if (b.type === NODE_FEATURE_HEADING && (b.attrs as FeatureHeadingAttrs | undefined)?.fid === fid) {
            i++;
            while (i < blocks.length && blocks[i].type !== NODE_FEATURE_HEADING) {
                if (blocks[i].type === NODE_PARAGRAPH) out.push(blocks[i]);
                i++;
            }
            return out;
        }
        i++;
    }
    return out;
}
