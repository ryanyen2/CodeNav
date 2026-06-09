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
import { wordDiff } from '../../state/doc-diff';
import { directionLabel, directionActions } from '../../state/grammar';
import type { Suggestion } from '../../state/suggestion-model';
import type { ThreadsData } from '../protocol';

export interface SuggestionHandlers {
    accept: (s: Suggestion) => void;
    reject: (s: Suggestion) => void;
    withdraw: (s: Suggestion) => void;
    /** Apply a doc-ahead suggestion: settle the change + queue the agent. */
    apply: (s: Suggestion) => void;
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

function diffSpans(oldStr: string, newStr: string): HTMLElement {
    const wrap = elc('span', 'ce-wd');
    for (const r of wordDiff(oldStr, newStr)) {
        wrap.append(elc('span', r.t === 'same' ? 'wd-same' : r.t === 'del' ? 'wd-del' : 'wd-ins', r.s));
    }
    return wrap;
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

function makeWidget(s: Suggestion, handlers: SuggestionHandlers): HTMLElement {
    const box = elc('div', `ce-diff ${s.direction} ${s.kind}`);
    box.contentEditable = 'false';
    box.setAttribute('data-suggestion', s.id);

    const head = elc('div', 'ce-diff-head');
    head.append(elc('span', 'ce-diff-dir', directionLabel(s.direction)));
    if (s.tag) head.append(elc('span', 'ce-diff-tag', s.tag));
    box.append(head);

    if (s.kind === 'add') {
        box.append(elc('div', 'ce-diff-row', `New feature: ${s.titleNew || '(untitled)'}`));
        if (s.descNew) box.append(elc('div', 'ce-diff-row ce-diff-desc', s.descNew));
    } else if (s.kind === 'retire') {
        box.append(elc('div', 'ce-diff-row', 'Retire this feature — detaches its bindings; the code itself is kept.'));
    } else if (s.kind === 'move') {
        box.append(elc('div', 'ce-diff-row', 'Move this feature to a new parent.'));
    } else {
        if ((s.titleOld || '') !== (s.titleNew || '')) {
            const row = elc('div', 'ce-diff-row');
            row.append(elc('span', 'ce-diff-label', 'title'), diffSpans(s.titleOld || '', s.titleNew || ''));
            box.append(row);
        }
        if ((s.descOld || '') !== (s.descNew || '')) {
            const row = elc('div', 'ce-diff-row');
            row.append(elc('span', 'ce-diff-label', 'desc'), diffSpans(s.descOld || '', s.descNew || ''));
            box.append(row);
        }
    }

    const actions = elc('div', 'ce-diff-actions');
    // Disable the row after the first click so the card can't fire twice while it's
    // still on screen (the authoritative removal arrives with the next payload).
    const once = (fn: (s: Suggestion) => void) => () => {
        actions.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
        actions.classList.add('applying');
        fn(s);
    };
    // One action pair per direction (grammar): code-ahead → Reject/Accept (human resolves);
    // doc-ahead → Withdraw/Apply (agent resolves).
    const [secondary, primary] = directionActions(s.direction);
    if (s.direction === 'code-ahead' && s.eventId) {
        actions.append(
            actionButton(secondary, 'reject', once(handlers.reject)),
            actionButton(primary, 'accept', once(handlers.accept)),
        );
    } else {
        actions.append(
            elc('span', 'ce-diff-await', 'your suggestion'),
            actionButton(secondary, 'withdraw', once(handlers.withdraw)),
            actionButton(primary, 'accept', once(handlers.apply)),
        );
    }
    box.append(actions);
    return box;
}

function buildDecorations(doc: PMModelNode, suggestions: Suggestion[], handlers: SuggestionHandlers): DecorationSet {
    const headingByFid = new Map<string, { pos: number; node: PMModelNode }>();
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading' && node.attrs.fid) {
            headingByFid.set(node.attrs.fid as string, { pos, node });
        }
    });

    const decos: Decoration[] = [];
    for (const s of suggestions) {
        if (s.kind === 'add') {
            const parent = s.parentId ? headingByFid.get(s.parentId) : null;
            const pos = parent ? parent.pos + parent.node.nodeSize : 0;
            decos.push(Decoration.widget(pos, () => makeWidget(s, handlers), { side: 1, key: 'sug-' + s.id }));
            continue;
        }
        const h = s.featureId ? headingByFid.get(s.featureId) : null;
        if (!h) continue;
        const after = h.pos + h.node.nodeSize;
        if (s.kind === 'retire') {
            decos.push(Decoration.node(h.pos, after, { class: 'ce-retire-proposed' }));
        }
        decos.push(Decoration.widget(after, () => makeWidget(s, handlers), { side: 1, key: 'sug-' + s.id }));
    }
    return DecorationSet.create(doc, decos);
}

// ── Unified dependency "threads" under each heading + on-demand peek (U4) ──────
// One quiet in-flow line per feature: `↳ reads … · ↰ used by … · ⟢ code refs …`,
// replacing the old ce-deps chips, the legacy xrefs, AND the tree-pane refs pill.
// Each strand shows a few named items + a "+N" that opens a peek popover with the full
// neighbourhood (client-side from the same payload — no extra round-trip; KTD5/H1).
export interface DependencyDecorationsOptions {
    getThreads: () => Record<string, ThreadsData>;
    onNavigate: (fid: string) => void;
    onOpenBinding: (file: string, symbol: string) => void;
}
export const DEPS_UPDATED = 'codocThreadsUpdated';
const depKey = new PluginKey('codocThreadDecorations');

const THREAD_MAX = 3; // named items per strand before a "+N" peek

function leafSym(symbol: string): string {
    const i = symbol.indexOf('::');
    const tail = i >= 0 ? symbol.slice(i + 2) : symbol;
    return tail === '__module__' ? '‹module›' : (tail.split('::').pop() ?? tail);
}

function threadsEmpty(t: ThreadsData): boolean {
    return !t.reads.length && !t.usedBy.length && !t.refs.length;
}

function threadLink(text: string, title: string, onClick: () => void): HTMLElement {
    const a = elc('span', 'ce-thread', text || '(untitled)');
    a.title = title;
    a.addEventListener('mousedown', ev => ev.preventDefault());
    a.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return a;
}

// ── peek popover (the full neighbourhood, client-side) ────────────────────────
let openPeekEl: HTMLElement | null = null;
function closePeek(): void { openPeekEl?.remove(); openPeekEl = null; }

function openThreadsPeek(
    anchor: HTMLElement, t: ThreadsData,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
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
    section('reads', t.reads.map(d => threadLink(d.toTitle, 'go to ' + d.toTitle, () => { closePeek(); onNavigate(d.toId); })));
    section('used by', t.usedBy.map(d => threadLink(d.toTitle, 'go to ' + d.toTitle, () => { closePeek(); onNavigate(d.toId); })));
    section('code refs', t.refs.map(r => threadLink(leafSym(r.symbol), r.file + ' › ' + leafSym(r.symbol), () => { closePeek(); onOpenBinding(r.file, r.symbol); })));
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
): HTMLElement {
    const row = elc('div', 'ce-threads');
    row.contentEditable = 'false';
    const strand = (glyph: string, glyphTitle: string, items: HTMLElement[], moreLabel: string): void => {
        if (!items.length) return;
        const s = elc('span', 'ce-strand');
        const g = elc('span', 'ce-strand-glyph', glyph); g.title = glyphTitle; s.append(g);
        items.slice(0, THREAD_MAX).forEach((el, i) => { if (i) s.append(document.createTextNode(', ')); s.append(el); });
        if (items.length > THREAD_MAX) {
            const more = elc('span', 'ce-more', `+${items.length - THREAD_MAX}`);
            more.title = `Show all ${items.length} ${moreLabel}`;
            more.addEventListener('mousedown', ev => ev.preventDefault());
            more.addEventListener('click', ev => { ev.preventDefault(); openThreadsPeek(row, t, onNavigate, onOpenBinding); });
            s.append(more);
        }
        row.append(s);
    };
    strand('↳', 'reads', t.reads.map(d => threadLink(d.toTitle, 'reads ' + d.toTitle + ' — go to it', () => onNavigate(d.toId))), 'reads');
    strand('↰', 'used by', t.usedBy.map(d => threadLink(d.toTitle, 'used by ' + d.toTitle + ' — go to it', () => onNavigate(d.toId))), 'used by');
    strand('⟢', 'code refs', t.refs.map(r => threadLink(leafSym(r.symbol), r.file + ' › ' + leafSym(r.symbol), () => onOpenBinding(r.file, r.symbol))), 'code refs');
    return row;
}

function buildThreadDecorations(
    doc: PMModelNode, threadsMap: Record<string, ThreadsData>,
    onNavigate: (fid: string) => void, onOpenBinding: (file: string, symbol: string) => void,
): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const t = threadsMap[fid];
        if (!t || threadsEmpty(t)) return;
        const after = pos + node.nodeSize;
        decos.push(Decoration.widget(after, () => makeThreadsRow(t, onNavigate, onOpenBinding), { side: -1, key: 'thr-' + fid }));
    });
    return DecorationSet.create(doc, decos);
}

export const DependencyDecorations = Extension.create<DependencyDecorationsOptions>({
    name: 'dependencyDecorations',
    addOptions() {
        return { getThreads: () => ({}), onNavigate: () => {}, onOpenBinding: () => {} };
    },
    addProseMirrorPlugins() {
        const getThreads = (): Record<string, ThreadsData> => this.options.getThreads();
        const onNavigate = this.options.onNavigate;
        const onOpenBinding = this.options.onOpenBinding;
        return [
            new Plugin({
                key: depKey,
                state: {
                    init: (_c, state) => buildThreadDecorations(state.doc, getThreads(), onNavigate, onOpenBinding),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(DEPS_UPDATED) || tr.docChanged) return buildThreadDecorations(newState.doc, getThreads(), onNavigate, onOpenBinding);
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
        return { getSuggestions: () => [], handlers: { accept: () => {}, reject: () => {}, withdraw: () => {}, apply: () => {} } };
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
