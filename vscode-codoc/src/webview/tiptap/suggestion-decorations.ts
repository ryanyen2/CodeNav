/**
 * suggestion-decorations.ts — anchors the resolution affordances for the suggestion
 * list in the whole-doc editor (R4). Since U3/U2b the human commits directly, so the
 * only suggestions here are agent code-ahead proposals (Reject / Accept → inbox.json
 * verdict). AMEND diffs render from the engine's insertion/deletion marks materialized
 * in the doc by the host (agent-proposals.applyAgentProposals); add/move/retire keep a
 * compact widget here. (Plus the per-feature "Connections" threads line below.)
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { directionLabel, directionActions } from '../../state/grammar';
import type { Suggestion } from '../../state/suggestion-model';
import type { ThreadsData, ThreadTarget } from '../protocol';
import { THREADS_COLLAPSE_AT } from '../protocol';

export interface SuggestionHandlers {
    accept: (s: Suggestion) => void;
    reject: (s: Suggestion) => void;
}

export interface SuggestionDecorationsOptions {
    getSuggestions: () => Suggestion[];
    handlers: SuggestionHandlers;
}

export const SUGGESTIONS_UPDATED = 'codocSuggestionsUpdated';
const decoKey = new PluginKey('codocSuggestionDecorations');

function elc(tag: string, cls?: string, text?: string): HTMLElement {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

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
    // All suggestions are agent code-ahead proposals (the human commits directly
    // since U3) → Reject / Accept, the human's verdict over the agent's change.
    const [secondary, primary] = directionActions(s.direction);
    actions.append(
        actionButton(secondary, 'reject', once(handlers.reject)),
        actionButton(primary, 'accept', once(handlers.accept)),
    );
    box.append(actions);
    return box;
}

// ── agent AMEND affordance (the tracked-change marks render the diff) ───────────
// The {old,new} diff is materialized as engine insertion/deletion marks in the doc
// (host-side agent-proposals.applyAgentProposals); the engine + CSS render the
// Google-Docs look (struck old / inserted new, agent-tinted). Here we only place the
// compact accept/reject affordance beneath the heading. add/move/retire keep their
// own compact widget (they can't be expressed as in-prose tracked changes).

/** The compact resolution affordance (direction marker + accept/reject). */
function amendActions(s: Suggestion, handlers: SuggestionHandlers): HTMLElement {
    const row = elc('div', 'ce-tc-actions ' + s.direction);
    const planned = isPlanned(s);
    const mark = elc('span', 'ce-tc-mark', planned ? '△' : '▲');
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
    actions.append(actionButton(secondary, 'reject', once(handlers.reject)),
                   actionButton(primary, 'accept', once(handlers.accept)));
    row.append(actions);
    return row;
}

interface FeatureLoc { headingPos: number; heading: PMModelNode; }

function buildDecorations(
    doc: PMModelNode, suggestions: Suggestion[], handlers: SuggestionHandlers,
): DecorationSet {
    // Locate every feature heading (the amend diff renders from in-doc marks now, so
    // we no longer need to track the description blocks for a hidden-overlay).
    const loc = new Map<string, FeatureLoc>();
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (fid) loc.set(fid, { headingPos: pos, heading: node });
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
        // amend — the tracked-change diff itself is materialized as engine ins/del
        // marks in the doc by the host (agent-proposals.applyAgentProposals); the
        // engine + CSS render it inline (struck old / inserted new, agent-tinted). We
        // only anchor the compact accept/reject affordance after the heading.
        decos.push(Decoration.widget(after, () => amendActions(s, handlers), { side: 1, key: 'sug-' + s.id }));
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
// Close the transient threads peek on window resize so it never sits at a stale position (U5).
if (typeof window !== 'undefined') window.addEventListener('resize', closePeek);

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
        return { getSuggestions: () => [], handlers: { accept: () => {}, reject: () => {} } };
    },

    addProseMirrorPlugins() {
        const getSuggestions = (): Suggestion[] => this.options.getSuggestions();
        const handlers = this.options.handlers;
        return [
            new Plugin({
                key: decoKey,
                state: {
                    init: (_config, state) => buildDecorations(state.doc, getSuggestions(), handlers),
                    apply: (tr, old, _oldState, newState) => {
                        if (tr.getMeta(SUGGESTIONS_UPDATED) || tr.docChanged) {
                            return buildDecorations(newState.doc, getSuggestions(), handlers);
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
