/**
 * suggestion-decorations.ts — renders the unified suggestion list as PERSISTENT
 * inline diff widgets in the whole-doc editor (R4). A diff sits beneath its
 * feature heading and stays until resolved:
 *   • code-ahead (agent → human): Reject / Accept → inbox.json verdict.
 *   • doc-ahead  (human → agent): "awaiting implementation" + Withdraw.
 * Word-level rendering over block-level {old,new} storage (doc-diff.ts).
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { wordDiff, compactRuns } from '../../state/doc-diff';
import { directionLabel, directionActions } from '../../state/grammar';
import { textToInlineRuns, codeRefToText, type CodeRefAttrs } from '../../state/pm-doc';
import type { Suggestion } from '../../state/suggestion-model';
import type { ThreadsData, ThreadTarget } from '../protocol';
import { THREADS_COLLAPSE_AT } from '../protocol';

export interface SuggestionHandlers {
    accept: (s: Suggestion) => void;
    reject: (s: Suggestion) => void;
    withdraw: (s: Suggestion) => void;
}

export interface SuggestionDecorationsOptions {
    getSuggestions: () => Suggestion[];
    handlers: SuggestionHandlers;
    /** The feature the caret is currently in — its suggestion renders as LIVE editable
     *  text (no tracked-change overlay) so the user keeps composing inline; every other
     *  suggested feature shows the inline tracked change. */
    getActiveFid: () => string | null;
}

export const SUGGESTIONS_UPDATED = 'codocSuggestionsUpdated';
const decoKey = new PluginKey('codocSuggestionDecorations');

function elc(tag: string, cls?: string, text?: string): HTMLElement {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

const WD_CLASS = { same: 'wd-same', del: 'wd-del', ins: 'wd-ins' } as const;

function actionButton(label: string, cls: string, onClick: () => void): HTMLButtonElement {
    const b = document.createElement('button');
    b.className = 'ce-diff-btn ' + cls;
    b.textContent = label;
    b.type = 'button';
    b.addEventListener('mousedown', ev => ev.preventDefault());
    b.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); onClick(); });
    return b;
}

/** A plan proposal describes code that does not exist yet (an unrealized
 *  placeholder); a reflection/drift proposal describes code that already landed.
 *  The two are encoded by TEXTURE (dashed vs solid) + marker fill (△ vs ▲) —
 *  never by a new colour (colour stays direction). */
function isPlanned(s: Suggestion): boolean {
    return s.direction === 'code-ahead' && (s.tag ?? '').includes('plan');
}

function makeWidget(s: Suggestion, handlers: SuggestionHandlers): HTMLElement {
    // Authorship rides on INK OPACITY (the pen/pencil axis): a human's own words
    // at full ink, an agent's proposal pencil-faded until accepted.
    const author = s.originRole === 'human' ? 'by-human' : 'by-agent';
    const planned = isPlanned(s) ? ' planned' : '';
    const box = elc('div', `ce-diff ${s.direction} ${s.kind} ${author}${planned}`);
    box.contentEditable = 'false';
    box.setAttribute('data-suggestion', s.id);

    // a tiny inline direction marker (▲ code-ahead / ▼ doc-ahead) — not a header
    // line. A plan proposal (code not yet real) hollows the marker: △.
    const glyph = s.direction === 'code-ahead' ? (planned ? '△' : '▲') : '▼';
    const mark = elc('span', 'ce-diff-mark', glyph);
    mark.title = directionLabel(s.direction)
        + (planned ? ' · not yet in code' : '')
        + (s.tag ? ' · ' + s.tag : '');
    box.append(mark);

    // Cascade cue: a non-empty causedBy means this surfaced back from implementing
    // one of the user's own doc edits — the lightest possible grouping (text only,
    // existing direction colour, no new surface).
    if (s.causedBy) {
        const cascade = elc('span', 'ce-diff-cascade', '↳ from your edit');
        cascade.title = `implements ${s.causedBy}`;
        box.append(cascade);
    }

    // The structural kind, compact. makeWidget only ever renders add / move / retire —
    // amend suggestions render inline via amendBlock (the tracked-change overlay), so
    // there is deliberately no amend branch here.
    const body = elc('span', 'ce-diff-body');
    if (s.kind === 'add') {
        body.append(elc('span', 'ce-diff-kind', '+ new'), document.createTextNode(' ' + (s.titleNew || '(untitled)')));
    } else if (s.kind === 'retire') {
        body.append(elc('span', 'ce-diff-kind', '~ retire'), document.createTextNode(' — detaches bindings; code kept'));
    } else if (s.kind === 'move') {
        body.append(elc('span', 'ce-diff-kind', '→ move'));
    }
    box.append(body);

    const actions = elc('span', 'ce-diff-actions');
    // Disable the row after the first click so the card can't fire twice while it's
    // still on screen (the authoritative removal arrives with the next payload).
    const once = (fn: (s: Suggestion) => void) => () => {
        actions.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
        actions.classList.add('applying');
        fn(s);
    };
    // Verdicts follow the grammar: code-ahead → Reject/Accept (the human is the
    // authority over the doc); doc-ahead → Withdraw only (the AI side applies it
    // — Loop B drains the intent, the agent implements; "accepting" your own
    // suggestion would be meaningless).
    const [secondary, primary] = directionActions(s.direction);
    if (s.direction === 'code-ahead' && s.eventId) {
        actions.append(
            actionButton(secondary, 'reject', once(handlers.reject)),
            actionButton(primary, 'accept', once(handlers.accept)),
        );
    } else {
        actions.append(
            elc('span', 'ce-diff-await', '→ for agent'),
            actionButton(secondary, 'withdraw', once(handlers.withdraw)),
        );
    }
    box.append(actions);
    return box;
}

// ── inline tracked-change rendering (the Google-Docs look) ─────────────────────
// An AMEND suggestion renders IN PLACE: the title diff is struck/inserted inline on
// the heading; the description's live paragraphs are hidden and the tracked change is
// rendered where they sat (strike removed words, underline inserted words, refs as
// chips). The proposed text is shown standalone from the {old,new} STRINGS — robust
// against ref atoms (no fragile char→position mapping over the live doc). When the
// caret is in the feature it renders nothing (the live editable prose shows through),
// so the user keeps composing the suggestion inline. add/move/retire keep a compact
// widget (they can't be expressed as in-prose tracked changes).

/** Render a diff run's text into `out`, turning `[label](codoc:…)` refs into chips so
 *  the tracked change reads like prose, not raw markdown. `cls` ∈ wd-same/del/ins. */
function runDom(text: string, cls: string, out: Node[]): void {
    for (const r of textToInlineRuns(text)) {
        if (r.type === 'text') { if (r.text) out.push(elc('span', cls, r.text)); }
        else if (r.type === 'codeRef') {
            const a = r.attrs as unknown as CodeRefAttrs;
            const chip = elc('span', cls + ' ce-tc-ref', a.label || leafSym(a.symbol ?? a.file) || a.file);
            chip.title = codeRefToText(a);
            out.push(chip);
        }
    }
}

/** Paragraph-aligned word-diff render of a description change. */
function trackedDesc(descOld: string, descNew: string): HTMLElement {
    const wrap = elc('div', 'ce-tc');
    const olds = descOld ? descOld.split(/\n{2,}/) : [];
    const news = descNew ? descNew.split(/\n{2,}/) : [];
    const n = Math.max(olds.length, news.length);
    for (let i = 0; i < n; i++) {
        const o = olds[i] ?? '';
        const ne = news[i] ?? '';
        const p = elc('p', 'ce-tc-p');
        const nodes: Node[] = [];
        if (o && !ne) runDom(o, 'wd-del', nodes);
        else if (!o && ne) runDom(ne, 'wd-ins', nodes);
        else if (o === ne) runDom(o, 'wd-same', nodes);
        else for (const run of compactRuns(wordDiff(o, ne))) runDom(run.s, WD_CLASS[run.t], nodes);
        nodes.forEach(x => p.append(x));
        wrap.append(p);
    }
    return wrap;
}

/** Inline title tracked-change over the heading (titles are ref-free → char offset ==
 *  PM position, 1:1): strike removed words in place, insert new words as inline widgets. */
function titleDecos(headingPos: number, titleOld: string, titleNew: string, key: string): Decoration[] {
    const decos: Decoration[] = [];
    const base = headingPos + 1; // heading content start
    let off = 0;
    for (const run of compactRuns(wordDiff(titleOld, titleNew))) {
        if (run.t === 'same') { off += run.s.length; }
        else if (run.t === 'del') {
            decos.push(Decoration.inline(base + off, base + off + run.s.length, { class: 'ce-tc-del-inline' }));
            off += run.s.length;
        } else {
            decos.push(Decoration.widget(base + off, () => elc('span', 'ce-tc-ins-inline', run.s), { side: 1, key: `${key}-ti-${off}` }));
        }
    }
    return decos;
}

/** The compact resolution affordance (direction marker + accept/reject or withdraw).
 *  Replaces the old full card — the change itself is shown inline above it. */
function amendActions(s: Suggestion, handlers: SuggestionHandlers): HTMLElement {
    const row = elc('div', 'ce-tc-actions ' + s.direction);
    const planned = isPlanned(s);
    const mark = elc('span', 'ce-tc-mark', s.direction === 'code-ahead' ? (planned ? '△' : '▲') : '▼');
    mark.title = directionLabel(s.direction) + (planned ? ' · not yet in code' : '') + (s.tag ? ' · ' + s.tag : '');
    row.append(mark);
    if (s.causedBy) {
        const c = elc('span', 'ce-tc-cascade', '↳ from your edit');
        c.title = `implements ${s.causedBy}`;
        row.append(c);
    }
    const actions = elc('span', 'ce-tc-btns');
    const once = (fn: (s: Suggestion) => void) => () => {
        actions.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
        actions.classList.add('applying');
        fn(s);
    };
    const [secondary, primary] = directionActions(s.direction);
    if (s.direction === 'code-ahead' && s.eventId) {
        actions.append(actionButton(secondary, 'reject', once(handlers.reject)), actionButton(primary, 'accept', once(handlers.accept)));
    } else {
        actions.append(elc('span', 'ce-tc-await', '→ for agent'), actionButton(secondary, 'withdraw', once(handlers.withdraw)));
    }
    row.append(actions);
    return row;
}

/** The inline tracked-change block placed where a feature's description sits (its live
 *  paragraphs are hidden by a node decoration while this shows). */
function amendBlock(s: Suggestion, handlers: SuggestionHandlers, descChanged: boolean): HTMLElement {
    const box = elc('div', `ce-tc-block ${s.direction}` + (isPlanned(s) ? ' planned' : '')
        + (s.originRole === 'human' ? ' by-human' : ' by-agent'));
    box.contentEditable = 'false';
    if (descChanged) box.append(trackedDesc(s.descOld ?? '', s.descNew ?? ''));
    box.append(amendActions(s, handlers));
    return box;
}

interface FeatureLoc { headingPos: number; heading: PMModelNode; descBlocks: { pos: number; node: PMModelNode }[]; }

function buildDecorations(
    doc: PMModelNode, suggestions: Suggestion[], handlers: SuggestionHandlers, activeFid: string | null,
): DecorationSet {
    // Locate every feature: its heading + the non-heading blocks beneath it.
    const loc = new Map<string, FeatureLoc>();
    let cur: FeatureLoc | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            cur = { headingPos: pos, heading: node, descBlocks: [] };
            if (fid) loc.set(fid, cur);
        } else if (cur && node.type.name === 'paragraph') {
            cur.descBlocks.push({ pos, node });
        }
    });

    const decos: Decoration[] = [];
    for (const s of suggestions) {
        if (s.kind === 'add') {
            const parent = s.parentId ? loc.get(s.parentId) : null;
            const pos = parent ? parent.headingPos + parent.heading.nodeSize : 0;
            decos.push(Decoration.widget(pos, () => makeWidget(s, handlers), { side: 1, key: 'sug-' + s.id }));
            continue;
        }
        const l = s.featureId ? loc.get(s.featureId) : null;
        if (!l) continue;
        const after = l.headingPos + l.heading.nodeSize;
        if (s.kind === 'move' || s.kind === 'retire') {
            if (s.kind === 'retire') decos.push(Decoration.node(l.headingPos, after, { class: 'ce-retire-proposed' }));
            decos.push(Decoration.widget(after, () => makeWidget(s, handlers), { side: 1, key: 'sug-' + s.id }));
            continue;
        }
        // amend — inline tracked change. Suppress the overlay only for a DOC-AHEAD
        // suggestion whose feature the caret is in (the user is composing it live, so
        // the live prose shows through). A CODE-AHEAD proposal must always render its
        // tracked change + accept/reject, even with the caret inside, or it becomes
        // non-actionable while editing that feature.
        if (s.direction === 'doc-ahead' && s.featureId === activeFid) continue;
        const titleOld = s.titleOld ?? '', titleNew = s.titleNew ?? '';
        const descChanged = (s.descOld ?? '') !== (s.descNew ?? '');
        if (titleOld !== titleNew && (l.heading.textContent || '') === titleOld) {
            for (const d of titleDecos(l.headingPos, titleOld, titleNew, s.id)) decos.push(d);
        }
        if (descChanged) {
            for (const b of l.descBlocks) decos.push(Decoration.node(b.pos, b.pos + b.node.nodeSize, { class: 'ce-tc-hidden' }));
        }
        decos.push(Decoration.widget(after, () => amendBlock(s, handlers, descChanged), { side: 1, key: 'sug-' + s.id }));
    }
    return DecorationSet.create(doc, decos);
}

// ── Unified "Connections" under each heading + on-demand peek (U4 → U5) ────────
// One quiet in-flow line per feature, four strands:
//   ↳ Depends-on (reads) · ↰ Used-by · ⟢ Bound code · ◷ Consult (external links)
// replacing the old ce-deps chips, the legacy xrefs, AND the tree-pane refs pill.
// reads/used-by are RANKED by coupling weight (heaviest first); each strand caps at
// THREADS_COLLAPSE_AT and, when it overflows, shows a "+N" that opens a peek popover
// with the full ranked neighbourhood (client-side from the same payload — no extra
// round-trip; KTD5/H1). The cap matches the assembler's `collapsed` flag.
// COLOUR = direction (reads vs used-by); SHAPE = kind (per-edge call/import marker).
export interface DependencyDecorationsOptions {
    getThreads: () => Record<string, ThreadsData>;
    onNavigate: (fid: string) => void;
    onOpenBinding: (file: string, symbol: string) => void;
    onConsult: (url: string) => void;
}
export const DEPS_UPDATED = 'codocThreadsUpdated';
const depKey = new PluginKey('codocThreadDecorations');

const THREAD_MAX = THREADS_COLLAPSE_AT; // named items per strand before a "+N" peek

// DISPLAY variant — deliberately NOT the canonical `symbolLeaf` (registry-model.ts):
// strips only the `file::` qualifier (keeps `Class.method`) and maps `__module__` to
// the `‹module›` glyph for the Connections "bound code" rows. Converging it would
// drop the `Class.` nesting from the displayed symbol label.
function leafSym(symbol: string): string {
    const i = symbol.indexOf('::');
    const tail = i >= 0 ? symbol.slice(i + 2) : symbol;
    return tail === '__module__' ? '‹module›' : (tail.split('::').pop() ?? tail);
}

/** Per-edge SHAPE glyph (shape = kind, never a new colour). A pure call edge →
 *  `()`; a pure import edge → `⊂`; mixed / unknown → none. Rendered as a quiet
 *  superscript marker on the feature link. */
function kindShape(kinds: string[] | undefined): string {
    const has = (k: string): boolean => (kinds ?? []).some(x => x.includes(k));
    const call = has('call');
    const imp = has('import');
    if (call && !imp) return '()';
    if (imp && !call) return '⊂';
    return '';
}

function threadsEmpty(t: ThreadsData): boolean {
    return !t.reads.length && !t.usedBy.length && !t.refs.length && !(t.consult ?? []).length;
}

function threadLink(text: string, title: string, onClick: () => void, fid?: string, shape?: string): HTMLElement {
    const a = elc('span', 'ce-thread', text || '(untitled)');
    a.title = title;
    // Tag a feature link with its fid so the hover-card handler (U4) can resolve it
    // by feature id — a decoration data-attr only, never serialized into the doc —
    // and make it keyboard-reachable so the card opens on Enter/Space.
    if (fid) { a.dataset.fid = fid; a.tabIndex = 0; }
    if (shape) a.append(elc('sup', 'ce-thread-kind', shape));
    a.addEventListener('mousedown', ev => ev.preventDefault());
    a.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return a;
}

/** A feature thread link carrying its shape (kind) marker. */
function featureLink(d: ThreadTarget, verb: string, onNavigate: (fid: string) => void): HTMLElement {
    return threadLink(d.toTitle, `${verb} ${d.toTitle} — go to it`, () => onNavigate(d.toId), d.toId, kindShape(d.kinds));
}

// ── peek popover (the full neighbourhood, client-side) ────────────────────────
let openPeekEl: HTMLElement | null = null;
function closePeek(): void { openPeekEl?.remove(); openPeekEl = null; }

function openThreadsPeek(
    anchor: HTMLElement, t: ThreadsData,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
    onConsult: (url: string) => void,
): void {
    closePeek();
    const pop = elc('div', 'ce-peek');
    const section = (label: string, items: HTMLElement[]): void => {
        if (!items.length) return;
        const sec = elc('div', 'ce-peek-sec');
        sec.append(elc('div', 'ce-peek-label', label));
        const list = elc('div', 'ce-peek-list');
        items.forEach(i => list.append(i));
        sec.append(list);
        pop.append(sec);
    };
    section('depends on', t.reads.map(d => threadLink(d.toTitle, 'go to ' + d.toTitle, () => { closePeek(); onNavigate(d.toId); }, d.toId, kindShape(d.kinds))));
    section('used by', t.usedBy.map(d => threadLink(d.toTitle, 'go to ' + d.toTitle, () => { closePeek(); onNavigate(d.toId); }, d.toId, kindShape(d.kinds))));
    section('bound code', t.refs.map(r => threadLink(leafSym(r.symbol), r.file + ' › ' + leafSym(r.symbol), () => { closePeek(); onOpenBinding(r.file, r.symbol); })));
    section('consult', (t.consult ?? []).map(l => threadLink(l.label, l.url, () => { closePeek(); onConsult(l.url); })));
    document.body.append(pop);
    const rect = anchor.getBoundingClientRect();
    pop.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - pop.offsetHeight - 8)}px`;
    pop.style.left = `${Math.min(rect.left, window.innerWidth - pop.offsetWidth - 8)}px`;
    openPeekEl = pop;
    // dismiss on outside click / Esc (registered next tick so the opening click doesn't close it)
    const cleanup = (): void => {
        document.removeEventListener('mousedown', onDoc, true);
        document.removeEventListener('keydown', onKey, true);
    };
    const onDoc = (e: MouseEvent): void => { if (!pop.contains(e.target as Node)) { closePeek(); cleanup(); } };
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') { closePeek(); cleanup(); } };
    setTimeout(() => {
        document.addEventListener('mousedown', onDoc, true);
        document.addEventListener('keydown', onKey, true);
    }, 0);
}

function makeThreadsRow(
    t: ThreadsData,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
    onConsult: (url: string) => void,
): HTMLElement {
    const row = elc('div', 'ce-threads');
    row.contentEditable = 'false';
    // dir ∈ reads (Depends-on) | used (Used-by) | refs (Bound code) | consult — the
    // CLASS, not a hue: colour = direction is applied in CSS off `.ce-strand.<dir>`.
    const strand = (dir: string, glyph: string, glyphTitle: string, items: HTMLElement[], moreLabel: string): void => {
        if (!items.length) return;
        const s = elc('span', 'ce-strand ' + dir);
        const g = elc('span', 'ce-strand-glyph', glyph); g.title = glyphTitle; s.append(g);
        items.slice(0, THREAD_MAX).forEach((el, i) => { if (i) s.append(document.createTextNode(', ')); s.append(el); });
        // Collapse: beyond THREADS_COLLAPSE_AT rows, a "+N" reveals the full ranked list
        // in the peek — a display swap (popover), no transition (reduced-motion safe).
        if (items.length > THREAD_MAX) {
            const more = elc('span', 'ce-more', `+${items.length - THREAD_MAX} more`);
            more.title = `Show all ${items.length} ${moreLabel}`;
            more.addEventListener('mousedown', ev => ev.preventDefault());
            more.addEventListener('click', ev => { ev.preventDefault(); openThreadsPeek(row, t, onNavigate, onOpenBinding, onConsult); });
            s.append(more);
        }
        row.append(s);
    };
    strand('reads', '↳', 'depends on', t.reads.map(d => featureLink(d, 'depends on', onNavigate)), 'depends on');
    strand('used', '↰', 'used by', t.usedBy.map(d => featureLink(d, 'used by', onNavigate)), 'used by');
    strand('refs', '⟢', 'bound code', t.refs.map(r => threadLink(leafSym(r.symbol), r.file + ' › ' + leafSym(r.symbol), () => onOpenBinding(r.file, r.symbol))), 'bound code');
    strand('consult', '◷', 'consult', (t.consult ?? []).map(l => threadLink(l.label, l.url, () => onConsult(l.url))), 'consult links');
    return row;
}

function buildThreadDecorations(
    doc: PMModelNode, threadsMap: Record<string, ThreadsData>,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
    onConsult: (url: string) => void,
): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const t = threadsMap[fid];
        if (!t || threadsEmpty(t)) return;
        const after = pos + node.nodeSize;
        decos.push(Decoration.widget(after, () => makeThreadsRow(t, onNavigate, onOpenBinding, onConsult), { side: -1, key: 'thr-' + fid }));
    });
    return DecorationSet.create(doc, decos);
}

export const DependencyDecorations = Extension.create<DependencyDecorationsOptions>({
    name: 'dependencyDecorations',
    addOptions() {
        return { getThreads: () => ({}), onNavigate: () => {}, onOpenBinding: () => {}, onConsult: () => {} };
    },
    addProseMirrorPlugins() {
        const getThreads = (): Record<string, ThreadsData> => this.options.getThreads();
        const onNavigate = this.options.onNavigate;
        const onOpenBinding = this.options.onOpenBinding;
        const onConsult = this.options.onConsult;
        return [
            new Plugin({
                key: depKey,
                state: {
                    init: (_c, state) => buildThreadDecorations(state.doc, getThreads(), onNavigate, onOpenBinding, onConsult),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(DEPS_UPDATED) || tr.docChanged) return buildThreadDecorations(newState.doc, getThreads(), onNavigate, onOpenBinding, onConsult);
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return depKey.getState(state); } },
            }),
        ];
    },
});

export const SuggestionDecorations = Extension.create<SuggestionDecorationsOptions>({
    name: 'suggestionDecorations',

    addOptions() {
        return { getSuggestions: () => [], getActiveFid: () => null, handlers: { accept: () => {}, reject: () => {}, withdraw: () => {} } };
    },

    addProseMirrorPlugins() {
        const getSuggestions = (): Suggestion[] => this.options.getSuggestions();
        const getActiveFid = (): string | null => this.options.getActiveFid();
        const handlers = this.options.handlers;
        return [
            new Plugin({
                key: decoKey,
                state: {
                    init: (_config, state) => buildDecorations(state.doc, getSuggestions(), handlers, getActiveFid()),
                    apply: (tr, old, _oldState, newState) => {
                        if (tr.getMeta(SUGGESTIONS_UPDATED) || tr.docChanged) {
                            return buildDecorations(newState.doc, getSuggestions(), handlers, getActiveFid());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: {
                    decorations(state) { return decoKey.getState(state); },
                },
            }),
        ];
    },
});
