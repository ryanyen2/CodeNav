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

export const MARK_AUTHOR = 'author';
// Tracked-change marks (vendored track-changes engine). `insertion` wraps
// not-yet-committed added text; `deletion` wraps text struck but still present in
// the baseline. The canonical `tree.codoc` projection is the BASELINE — see
// `inlineRunsToText`.
export const MARK_INSERTION = 'insertion';
export const MARK_DELETION = 'deletion';

/** Commitment mode — drives OPACITY (pen solid, pencil faded). */
export type AuthorMode = 'pen' | 'pencil';
/** Who authored a span — drives COLOR/tint. Open-ended on purpose (new agents). */
export type AuthorRole = 'human' | 'claude-code' | 'codex' | 'gemini' | 'cursor' | string;

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
    /** Stable client-side identity (KTD8), minted before the daemon assigns `fid`. The
     *  diff keys on it (Step 3) and the uniqueness plugin (Step 5) keeps it distinct per
     *  live node. Optional in the model type — only authored headings carry one. */
    localId?: string | null;
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

/**
 * A description paragraph. `ownerId` (the fid|localId of the feature it belongs to)
 * anchors the prose to its feature by IDENTITY rather than by "the nearest heading
 * above it right now" (invariant I2). It is stamped at projection time (the Python
 * `build_doc_from_store` seam) and crystallized onto brand-new prose by the keep-owner
 * plugin, then preserved by ProseMirror across split/merge — so inserting a heading
 * above owned prose never re-attributes it. `null`/omitted → attribution falls back to
 * position (a paragraph with no owner yet), keeping older docs byte-identical.
 */
export function paragraphNode(content: PMNode[], ownerId: string | null = null): PMNode {
    return ownerId
        ? { type: NODE_PARAGRAPH, attrs: { ownerId }, content }
        : { type: NODE_PARAGRAPH, content };
}

/** The feature identity a paragraph is anchored to (invariant I2), or null if unowned. */
export function paragraphOwner(node: PMNode): string | null {
    return (node.attrs as { ownerId?: string | null } | undefined)?.ownerId ?? null;
}

/**
 * For a whole-tree doc, compute the owner each UN-owned paragraph should adopt: the
 * identity (fid ?? localId) of the nearest preceding heading. Returns a map from
 * top-level block index → ownerId to stamp; already-owned paragraphs, non-paragraphs,
 * and prose before the first heading are absent (no fill). This is the pure logic the
 * keep-owner ProseMirror plugin applies (paragraph-owner.ts) — kept here so it is
 * testable without a live editor and so the attribution model has one definition.
 */
export function paragraphOwnerFills(doc: PMNode): Map<number, string> {
    const blocks = doc.content ?? [];
    const fills = new Map<number, string>();
    let nearest: string | null = null;
    blocks.forEach((b, i) => {
        if (b.type === NODE_FEATURE_HEADING) {
            const a = b.attrs as { fid?: string | null; localId?: string | null } | undefined;
            nearest = (a?.fid ?? a?.localId) ?? null;
        } else if (b.type === NODE_PARAGRAPH) {
            if (!paragraphOwner(b) && nearest) fills.set(i, nearest);
        }
    });
    return fills;
}

export function featureHeadingNode(attrs: FeatureHeadingAttrs, content: PMNode[]): PMNode {
    return { type: NODE_FEATURE_HEADING, attrs: { ...attrs }, content };
}

export function makeDoc(content: PMNode[]): PMNode {
    return { type: NODE_DOC, content };
}

// ── inline ↔ text projection ─────────────────────────────────────────────────

/**
 * Inline citation regex — IDENTICAL to `parse.extract_refs` / `tree-model.extractRefs`
 * (kept as its own copy so this module imports nothing).  `[label](codoc:file#symbol)`
 * with an optional `#symbol`.
 */
export const REF_RE_SOURCE = '\\[([^\\]]*)\\]\\(codoc:([^)#]+)(?:#([^)]+))?\\)';

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
 *
 * Tracked-change BASELINE projection: a run carrying an `insertion` mark is a
 * not-yet-accepted addition, so it is EXCLUDED (it must not leak into the committed
 * canonical text); a `deletion`-marked run is struck but still part of the baseline,
 * so its text is KEPT (the mark dropped like any other). A human edit that committed
 * directly carries no tracked marks and is emitted normally. This keeps `tree.codoc`
 * equal to "what is committed before pending agent proposals are resolved."
 */
export function inlineRunsToText(content: PMNode[] | undefined): string {
    let s = '';
    for (const n of content ?? []) {
        if (n.marks?.some(m => m.type === MARK_INSERTION)) continue; // uncommitted insertion — excluded from baseline
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
 * Canonical form for a feature description (R19) — the TS mirror of
 * `parse.normalize_description` (codoc/codoc_file/parse.py). Strip each line, drop
 * leading/trailing blank lines, collapse interior blank-line runs to one. Keeping
 * the host's serialized description canonical means a trailing-whitespace-only edit
 * never round-trips to a phantom diff against the daemon's parser. Must stay byte-for
 * byte equal to the Python form — guarded by the parity test in doc-roundtrip.test.ts.
 */
export function normalizeDescription(text: string): string {
    const lines = (text ?? '').split('\n').map(l => l.trim());
    while (lines.length && !lines[0]) lines.shift();
    while (lines.length && !lines[lines.length - 1]) lines.pop();
    const collapsed: string[] = [];
    for (const ln of lines) {
        if (!ln && collapsed.length && !collapsed[collapsed.length - 1]) continue;
        collapsed.push(ln);
    }
    return collapsed.join('\n');
}

/**
 * Paragraph blocks → description string (inverse of `descriptionToBlocks`).
 * Empty paragraphs are dropped; the rest join with a blank line, then the result is
 * canonicalized (`normalizeDescription`) so the host's serialized text matches the
 * daemon's parser. Marks (bold, author, …) are projected away — only text + codeRef
 * markdown survive, matching what `tree.codoc` can carry.
 */
export function blocksToDescriptionText(blocks: PMNode[]): string {
    return normalizeDescription(blocks
        .filter(b => b.type === NODE_PARAGRAPH)
        .map(b => inlineRunsToText(b.content))
        .filter(s => s.trim().length > 0)
        .join('\n\n'));
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
