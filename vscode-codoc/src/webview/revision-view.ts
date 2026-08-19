/**
 * revision-view.ts — the document as it read then (W8).
 *
 * The read-only page the timeline scrubber shows: the tree reconstructed at a chosen
 * moment, with the change made AT that moment marked in the prose where it happened —
 * old words struck where they stood, new words underlined.
 *
 * ## Why this is not the editor
 *
 * The obvious implementation swaps the historical text into the live TipTap editor. It
 * is also the dangerous one: the editor is wired to a settle→command pipeline whose
 * entire job is to notice that the document changed and tell the daemon about it. Push
 * Tuesday's prose through it and the store faithfully records that the author reverted
 * every feature in the tree. A separate, non-editable surface makes that class of
 * accident structurally impossible rather than carefully avoided.
 *
 * It is still IN SITU. Same pane, same measure, same typography, same scroll position —
 * the page you were reading, at that time. The classes here are deliberately the
 * editor's own (`codoc-feature-heading`, `codoc-code-ref`), so the two surfaces cannot
 * drift into looking like different documents.
 *
 * ## Why plain-string diffing
 *
 * The in-editor diff layers work in ProseMirror display space, where a decoration must
 * land on an exact document position and an inline atom counts as its own width. None
 * of that applies to DOM we are generating ourselves: here a diff run maps directly to
 * a text node, an `<ins>`, or a `<del>`. `wordDiff` — the same script-aware core the
 * editor's layers use — is the whole engine.
 */
import { wordDiff, type DiffRun } from '../state/doc-diff';
import { alignParas } from './tiptap/display-text';
import { REF_RE_SOURCE } from '../state/pm-doc';
import { preorder, type FeatureChange, type Snapshot } from '../state/revision-model';

/** One paragraph of a reconstructed description: the diff runs to render, and whether
 *  the paragraph itself is wholly gone (rendered struck, in place). */
export interface ParaDiff {
    runs: DiffRun[];
    /** True when this paragraph existed before the moment and not after. */
    removed: boolean;
}

/**
 * Per-paragraph diff runs between two descriptions.
 *
 * Paragraphs are PAIRED before they are diffed (`alignParas`), never zipped by index:
 * inserting a paragraph in the middle shifts every later one, and an index-paired diff
 * would then report the entire rest of the description as rewritten. A baseline
 * paragraph with no partner is emitted struck, in the position it held relative to the
 * paragraphs that survived — "this paragraph was here" is what a reader scrubbing back
 * is looking for.
 *
 * Two rules keep it honest, and both were bugs first:
 *
 * - A baseline paragraph is flushed only once the pairing says we have PASSED it. An
 *   inserted paragraph knows nothing about where we are in the baseline, so it must
 *   flush nothing; treating it as "we are at the end of the baseline" dumped every
 *   remaining deletion above the surviving text.
 * - A paragraph that merely MOVED is not a deletion. Its text is unchanged and it is
 *   already being drawn at its new home, so striking it as well would show the reader
 *   the same paragraph twice and claim it was removed when it is still there.
 */
export function paraDiffs(before: string, after: string): ParaDiff[] {
    const split = (s: string): string[] => (s.trim() ? s.split(/\n{2,}/) : []);
    const base = split(before);
    const cur = split(after);
    const pairing = alignParas(base, cur);
    const paired = new Set(pairing.filter((i): i is number => i != null));
    // Text that shows up as an UNPAIRED current paragraph: if a baseline paragraph reads
    // the same, it moved rather than died.
    const moved = new Set(cur.filter((_t, k) => pairing[k] == null));
    const survives = (i: number): boolean => paired.has(i) || moved.has(base[i]);

    const out: ParaDiff[] = [];
    const strike = (i: number): void => {
        if (!survives(i)) out.push({ runs: [{ t: 'del', s: base[i] }], removed: true });
    };
    let cursor = 0;                       // how far into the baseline the pairing has walked
    cur.forEach((text, k) => {
        const bi = pairing[k];
        if (bi != null) {
            for (let i = cursor; i < bi; i++) strike(i);
            cursor = bi + 1;
        }
        out.push({ runs: wordDiff(bi == null ? '' : base[bi], text), removed: false });
    });
    for (let i = cursor; i < base.length; i++) strike(i);
    return out;
}

// ── DOM ──────────────────────────────────────────────────────────────────────

const REF_RE = new RegExp(REF_RE_SOURCE, 'g');

export interface RevisionViewOptions {
    /** Open a bound code location (the same target a `codoc:` chip has in the editor).
     *  A historical page is still a real reading surface — its citations should work. */
    onOpenRef?: (file: string, symbol: string) => void;
    /** The reader clicked a feature; the caller syncs its own selection. */
    onSelect?: (fid: string) => void;
    /** Whether the viewed moment was the AUTHOR's, from the ledger's `actor`. It picks
     *  the channel the moment's diff is drawn in, so the past reads in the same grammar
     *  as the live page instead of a private one of its own (see `runNodes`). */
    human?: boolean;
}

/** Render one plain-text run, turning `[label](codoc:file#symbol)` into the same chip
 *  the editor draws, so a citation is still a citation in the past. */
function textRun(text: string, opts: RevisionViewOptions): (Node)[] {
    const out: Node[] = [];
    REF_RE.lastIndex = 0;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = REF_RE.exec(text)) !== null) {
        if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
        const chip = document.createElement('span');
        chip.className = 'codoc-code-ref';
        chip.textContent = m[1] || m[2];
        const file = m[2];
        const symbol = m[3] ?? '';
        chip.title = symbol ? `${file} · ${symbol}` : file;
        if (opts.onOpenRef) {
            chip.setAttribute('role', 'link');
            chip.tabIndex = 0;
            chip.addEventListener('click', ev => { ev.preventDefault(); opts.onOpenRef?.(file, symbol); });
        }
        out.push(chip);
        last = m.index + m[0].length;
    }
    if (last < text.length) out.push(document.createTextNode(text.slice(last)));
    return out;
}

/**
 * A moment's diff runs, in the settlement grammar.
 *
 * The past used to have a private encoding — `ce-past-add` / `ce-past-del`, an underline
 * and a strike in nobody's ink — and that was a second visual language for the same fact
 * the live document already spends three channels saying. A reader dragging the scrubber
 * had to translate: on the live page blue means "yours" and a green ground means "the
 * code says this", and on the past page both were the same grey underline.
 *
 * So a past run is drawn in the CHANNEL OF WHOEVER MADE IT, which the ledger already
 * records as the moment's `actor`. The stage is fixed: everything in history is settled
 * by definition, so a human moment is `committed` (never `open` — there are no unsent
 * edits in the past, and the pulse would be a prompt to act on something finished) and a
 * machine moment is `landed`.
 */
function runNodes(runs: DiffRun[], opts: RevisionViewOptions): Node[] {
    const channel = opts.human ? 'human committed' : 'code landed';
    const out: Node[] = [];
    for (const r of runs) {
        if (!r.s) continue;
        if (r.t === 'same') { out.push(...textRun(r.s, opts)); continue; }
        const el = document.createElement(r.t === 'ins' ? 'ins' : 'del');
        el.className = 'ce-settle ' + channel + (r.t === 'ins' ? ' add' : ' cut');
        el.append(...textRun(r.s, opts));
        out.push(el);
    }
    return out;
}

function plainParagraphs(description: string, opts: RevisionViewOptions): HTMLElement[] {
    if (!description.trim()) return [];
    return description.split(/\n{2,}/).map(text => {
        const p = document.createElement('p');
        p.append(...textRun(text, opts));
        return p;
    });
}

/**
 * Build the historical page.
 *
 * `changes` are the changes made AT the viewed moment; everything else renders plainly,
 * because a page where every paragraph is marked is a page where nothing is.
 *
 * Retired features are spliced back in at the position they held BEFORE the moment, so
 * "this section used to be here, and this is what it said" is answerable — which is the
 * one question about a deleted node that its absence cannot answer.
 */
export function renderRevisionPage(
    before: Snapshot,
    after: Snapshot,
    changes: FeatureChange[],
    opts: RevisionViewOptions = {},
): HTMLElement {
    const page = document.createElement('div');
    page.className = 'ce-past-doc';

    const changeByFid = new Map(changes.map(c => [c.fid, c]));
    const rows = preorder(after);
    // Splice removed features back in where they stood, using the pre-moment order.
    const beforeOrder = preorder(before);
    for (const c of changes) {
        if (c.after || !c.before) continue;
        const wasAt = beforeOrder.findIndex(r => r.fid === c.fid);
        const level = wasAt >= 0 ? beforeOrder[wasAt].level : 0;
        // Land it after whichever surviving neighbour preceded it, so it reappears in
        // context rather than at the end of the page.
        let insertAt = rows.length;
        for (let i = wasAt - 1; i >= 0; i--) {
            const at = rows.findIndex(r => r.fid === beforeOrder[i].fid);
            if (at >= 0) { insertAt = at + 1; break; }
        }
        rows.splice(insertAt, 0, { fid: c.fid, level });
    }

    for (const { fid, level } of rows) {
        const change = changeByFid.get(fid);
        const feature = after.features.get(fid) ?? change?.before ?? before.features.get(fid);
        if (!feature) continue;

        const section = document.createElement('section');
        section.className = 'ce-past-feature';
        section.dataset.fid = fid;
        if (change) {
            const kind = !change.before ? 'added' : !change.after ? 'removed' : 'edited';
            section.classList.add('changed', `ce-past-${kind}`);
        }
        if (opts.onSelect) section.addEventListener('click', () => opts.onSelect?.(fid));

        const heading = document.createElement('div');
        heading.className = 'codoc-feature-heading';
        heading.dataset.level = String(level);
        const beforeTitle = change?.before?.title ?? '';
        const afterTitle = change?.after?.title ?? feature.title;
        if (change && change.before && change.after && beforeTitle !== afterTitle) {
            heading.append(...runNodes(wordDiff(beforeTitle, afterTitle), opts));
        } else {
            heading.append(...textRun(afterTitle || beforeTitle, opts));
        }
        section.append(heading);

        if (change?.unresolved) {
            // The honest failure. An event written before codoc recorded what it
            // displaced can say that this feature changed and not what it changed from;
            // drawing a diff here would mean inventing the previous words.
            const note = document.createElement('p');
            note.className = 'ce-past-unresolved';
            note.textContent = 'codoc recorded that this changed, but not what it said before — '
                + 'this change predates the history it would need.';
            section.append(note);
            section.append(...plainParagraphs(feature.description, opts));
        } else if (change && (change.before?.description ?? '') !== (change.after?.description ?? '')) {
            for (const pd of paraDiffs(change.before?.description ?? '', change.after?.description ?? '')) {
                const p = document.createElement('p');
                if (pd.removed) p.classList.add('ce-past-gone');
                p.append(...runNodes(pd.runs, opts));
                section.append(p);
            }
        } else {
            section.append(...plainParagraphs(feature.description, opts));
        }
        page.append(section);
    }

    if (!rows.length) {
        const empty = document.createElement('p');
        empty.className = 'ce-past-empty';
        empty.textContent = 'The tree was empty at this point.';
        page.append(empty);
    }
    return page;
}
