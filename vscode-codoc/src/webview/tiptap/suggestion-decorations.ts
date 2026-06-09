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
import type { Suggestion } from '../../state/suggestion-model';
import type { FeatureDep } from '../protocol';

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
    const dir = s.direction === 'code-ahead' ? '▲ from code' : '▼ your edit';
    head.append(elc('span', 'ce-diff-dir', dir));
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
    if (s.direction === 'code-ahead' && s.eventId) {
        // Agent → human: the human resolves.
        actions.append(
            actionButton('Reject', 'reject', once(handlers.reject)),
            actionButton('Accept', 'accept', once(handlers.accept)),
        );
    } else {
        // Human → agent: Apply settles + sends the directive; Withdraw discards.
        actions.append(
            elc('span', 'ce-diff-await', 'your suggestion'),
            actionButton('Withdraw', 'withdraw', once(handlers.withdraw)),
            actionButton('Apply', 'accept', once(handlers.apply)),
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

// ── Dependency ("see also") chips under each heading ──────────────────────────
export interface DependencyDecorationsOptions {
    getDeps: () => Record<string, FeatureDep[]>;
    onNavigate: (fid: string) => void;
}
export const DEPS_UPDATED = 'codocDepsUpdated';
const depKey = new PluginKey('codocDepDecorations');

function makeDepsRow(deps: FeatureDep[], onNavigate: (fid: string) => void): HTMLElement {
    const row = elc('div', 'ce-deps');
    row.contentEditable = 'false';
    for (const d of deps) {
        const chip = elc('span', 'ce-dep ' + d.rel);
        chip.append(elc('span', 'ce-dep-rel', d.rel === 'depends' ? '↳' : '↰'), document.createTextNode(' ' + (d.toTitle || '(untitled)')));
        chip.title = d.rel === 'depends' ? 'depends on' : 'used by';
        chip.addEventListener('mousedown', ev => ev.preventDefault());
        chip.addEventListener('click', ev => { ev.preventDefault(); onNavigate(d.toId); });
        row.append(chip);
    }
    return row;
}

function buildDepDecorations(doc: PMModelNode, depsMap: Record<string, FeatureDep[]>, onNavigate: (fid: string) => void): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const deps = depsMap[fid];
        if (!deps || !deps.length) return;
        const after = pos + node.nodeSize;
        decos.push(Decoration.widget(after, () => makeDepsRow(deps, onNavigate), { side: -1, key: 'dep-' + fid }));
    });
    return DecorationSet.create(doc, decos);
}

export const DependencyDecorations = Extension.create<DependencyDecorationsOptions>({
    name: 'dependencyDecorations',
    addOptions() {
        return { getDeps: () => ({}), onNavigate: () => {} };
    },
    addProseMirrorPlugins() {
        const getDeps = (): Record<string, FeatureDep[]> => this.options.getDeps();
        const onNavigate = this.options.onNavigate;
        return [
            new Plugin({
                key: depKey,
                state: {
                    init: (_c, state) => buildDepDecorations(state.doc, getDeps(), onNavigate),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(DEPS_UPDATED) || tr.docChanged) return buildDepDecorations(newState.doc, getDeps(), onNavigate);
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
