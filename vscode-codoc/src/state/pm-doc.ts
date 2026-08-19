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
// StarterKit's bold. NOT decoration: a bolded span is codoc's focus signal — the
// daemon's `extract_bold` lifts it out of the description into the realize
// directive's `Focus:` line. It survives the text projection as literal `**…**`
// (see `inlineRunsToText`); before that it was silently dropped on every save, so
// the one authoring signal the prompts document was unreachable from the editor.
export const MARK_BOLD = 'bold';
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

/** The per-feature HLC the store projection stamps on each `featureHeading`
 *  (`feature.updated_at.to_str()`). Empty for a heading the store has not seen yet.
 *  This is the version an edit is made AGAINST — the version gate compares it, and
 *  a command's identity is derived from it. */
export function headingVersion(node: PMNode): string {
    const v = (node.attrs as { version?: unknown } | undefined)?.version;
    return typeof v === 'string' ? v : '';
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

/**
 * `**bold**` — IDENTICAL to `parse._BOLD_RE` (codoc/codoc_file/parse.py), including
 * the `[^*\n]+` content class. `extract_bold` is the only reader of the author's
 * focus signal, so a `**…**` the editor emits that this regex would NOT match is a
 * pair of asterisks in somebody's prose and nothing else.
 */
export const BOLD_RE_SOURCE = '\\*\\*([^*\\n]+)\\*\\*';

/** Serialize one codeRef to its canonical `[label](codoc:file#symbol)` text. */
export function codeRefToText(attrs: CodeRefAttrs): string {
    const target = attrs.symbol ? `${attrs.file}#${attrs.symbol}` : attrs.file;
    return `[${attrs.label}](codoc:${target})`;
}

/** The plain text one inline run projects to, before any mark handling. */
function runText(n: PMNode): string {
    if (n.type === NODE_TEXT) return n.text ?? '';
    if (n.type === NODE_CODE_REF && n.attrs) return codeRefToText(n.attrs as unknown as CodeRefAttrs);
    if (n.type === NODE_HARD_BREAK) return '\n';
    return '';
}

/** Would `**s**` be read back as one bold span? Mirrors `BOLD_RE_SOURCE`'s content
 *  class plus `extract_bold`'s strip-and-drop-empties: at least one non-space char,
 *  no `*`, no newline. Text that fails this is emitted UNWRAPPED — asterisks the
 *  daemon reads as prose are worse than a lost mark, because they change the stored
 *  description while signalling nothing. */
function boldReadableBack(s: string): boolean {
    return /\S/.test(s) && !s.includes('*') && !s.includes('\n');
}

/**
 * Concatenate inline runs into their plain-text projection (what lands in
 * `tree.codoc`): text verbatim, codeRef → markdown link, hardBreak → "\n".
 * Presentation marks (comment/author) are DROPPED — they live only in the store.
 *
 * `bold` is the exception, because it is not presentation: it is the author's focus
 * signal, and the daemon reads it out of the description text with a regex. So a
 * maximal RUN of bold-marked nodes is wrapped in one `**…**` — one wrapper per run,
 * not per node, since a span covering a citation arrives as text + codeRef + text and
 * wrapping each would bury `**` inside the link target.
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
    let boldRun = '';   // the current maximal run of bold-marked nodes
    function flushBold(): void {
        // An all-insertion bold run projects to nothing; wrapping it would emit `****`.
        s += boldReadableBack(boldRun) ? `**${boldRun}**` : boldRun;
        boldRun = '';
    }
    for (const n of content ?? []) {
        if (n.marks?.some(m => m.type === MARK_INSERTION)) continue; // uncommitted insertion — excluded from baseline
        const t = runText(n);
        // A hard break can never sit inside `**…**` (the regex stops at a newline), so
        // it closes the run instead of poisoning it into unreadable markup.
        if (n.type === NODE_HARD_BREAK) { flushBold(); s += t; continue; }
        if (n.marks?.some(m => m.type === MARK_BOLD)) { boldRun += t; continue; }
        flushBold();
        s += t;
    }
    flushBold();
    return s;
}

interface RefMatch { start: number; end: number; node: PMNode }

/**
 * The `**…**` spans a paragraph projects as a bold mark, as FULL match ranges
 * (markers included), in document order. The skipped cases below are each a match
 * that would NOT survive the trip back through `inlineRunsToText` — leaving them as
 * prose keeps the text on disk byte-identical instead of drifting one asterisk at a
 * time. Mirrored in `doc_render._bold_matches`.
 */
function boldMatches(text: string, refs: RefMatch[]): Array<{ start: number; end: number }> {
    const out: Array<{ start: number; end: number }> = [];
    const re = new RegExp(BOLD_RE_SOURCE, 'g');
    let prevEnd = -1;
    for (let m = re.exec(text); m; m = re.exec(text)) {
        const start = m.index;
        const end = start + m[0].length;
        // `**  **` carries no focus at all — `extract_bold` strips it to nothing.
        if (!/\S/.test(m[1])) continue;
        // A `**` INSIDE a citation is part of its label (`[**x**](codoc:a.py)`), not
        // markup: eating those asterisks would rewrite the link text. Bold that
        // CONTAINS a whole citation is the normal case and stays.
        const insideRef = (i: number): boolean => refs.some(r => i > r.start && i < r.end);
        if (insideRef(start) || insideRef(end - 2)) continue;
        // Two matches that touch (`**a****b**`) would project as adjacent bold runs,
        // and the serializer emits ONE wrapper per run — so they would come back as
        // `**ab**`: a phantom AMEND against text nobody edited. Leave the second prose.
        if (start === prevEnd) continue;
        out.push({ start, end });
        prevEnd = end;
    }
    return out;
}

/**
 * Split prose into inline runs at `[label](codoc:…)` and `**bold**` boundaries — the
 * inverse of `inlineRunsToText`. Keeps the RAW label (so an empty `[]` round-trips)
 * and emits codeRef *nodes*; the `**` markers are consumed into a `bold` mark so the
 * author sees emphasis rather than asterisks. Empty text slices are never emitted
 * (ProseMirror forbids empty text nodes).
 */
export function textToInlineRuns(text: string): PMNode[] {
    const refs: RefMatch[] = [];
    const re = new RegExp(REF_RE_SOURCE, 'g');
    for (let m = re.exec(text); m; m = re.exec(text)) {
        refs.push({
            start: m.index,
            end: m.index + m[0].length,
            node: codeRefNode({ label: m[1], file: m[2], symbol: m[3] ?? null }),
        });
    }
    const refAt = new Map(refs.map(r => [r.start, r]));
    // Per-character classification rather than a cut list: the two structures can nest
    // (bold around a citation) and reasoning about their boundaries pairwise is how an
    // off-by-two lands a `**` inside a link target.
    const marker = new Array<boolean>(text.length).fill(false);
    const bold = new Array<boolean>(text.length).fill(false);
    for (const b of boldMatches(text, refs)) {
        for (let i = b.start; i < b.end; i++) bold[i] = true;
        marker[b.start] = marker[b.start + 1] = marker[b.end - 2] = marker[b.end - 1] = true;
    }
    const boldMark = (): PMMark[] => [{ type: MARK_BOLD }];
    const runs: PMNode[] = [];
    let buf = '';
    let bufStart = 0;
    function flush(): void {
        if (buf) runs.push(textNode(buf, bold[bufStart] ? boldMark() : undefined));
        buf = '';
    }
    for (let i = 0; i < text.length;) {
        const ref = refAt.get(i);
        if (ref) {
            flush();
            runs.push(bold[i] ? { ...ref.node, marks: boldMark() } : ref.node);
            i = ref.end;
            continue;
        }
        if (marker[i]) { flush(); i++; continue; }
        if (buf && bold[bufStart] !== bold[i]) flush();
        if (!buf) bufStart = i;
        buf += text[i];
        i++;
    }
    flush();
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
 * daemon's parser. Presentation marks (author, comment) are projected away; `bold`
 * is written back as `**…**` because `tree.codoc` carries it — see `inlineRunsToText`.
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
