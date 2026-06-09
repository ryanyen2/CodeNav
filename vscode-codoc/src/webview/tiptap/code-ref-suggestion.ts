/**
 * code-ref-suggestion.ts — `@`-triggered autocomplete that inserts a `codeRef`
 * chip (U5). Symbols come from the sidecar `by_file` (bound symbols only — the
 * same source as the plain-text `completion.ts`), passed in from the host via the
 * payload, so no new index export is needed. Selecting an item inserts a `codeRef`
 * node that serializes back to `[label](codoc:file#symbol)`.
 */
import { Extension } from '@tiptap/core';
import Suggestion, { SuggestionProps, SuggestionKeyDownProps } from '@tiptap/suggestion';
import { PluginKey } from '@tiptap/pm/state';
import type { RefSymbol } from '../protocol';

export type { RefSymbol };

export interface CodeRefSuggestionOptions {
    getSymbols: () => RefSymbol[];
    char: string;
}

const suggestionKey = new PluginKey('codocCodeRefSuggestion');

/** A tiny keyboard-navigable popup, rendered into document.body and positioned at
 *  the caret. No framework — vanilla DOM, themed via `var(--vscode-*)`. */
function makePopup() {
    let root: HTMLElement | null = null;
    let items: RefSymbol[] = [];
    let selected = 0;
    let onPick: ((item: RefSymbol) => void) | null = null;

    const draw = (): void => {
        if (!root) return;
        root.replaceChildren();
        if (items.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'cr-empty';
            empty.textContent = 'No bound symbols match';
            root.append(empty);
            return;
        }
        items.forEach((it, i) => {
            const row = document.createElement('div');
            row.className = 'cr-item' + (i === selected ? ' active' : '');
            const name = document.createElement('span');
            name.className = 'cr-name';
            name.textContent = it.label;
            const det = document.createElement('span');
            det.className = 'cr-detail';
            det.textContent = it.detail ?? it.file;
            row.append(name, det);
            row.addEventListener('mousedown', ev => { ev.preventDefault(); onPick?.(it); });
            row.addEventListener('mouseenter', () => { selected = i; draw(); });
            root!.append(row);
        });
    };

    const place = (rect: DOMRect | null): void => {
        if (!root || !rect) return;
        root.style.top = `${rect.bottom + 4}px`;
        root.style.left = `${rect.left}px`;
    };

    const exit = (): void => {
        root?.remove();
        root = null;
        items = [];
        onPick = null;
    };

    return {
        onStart(props: SuggestionProps<RefSymbol>): void {
            items = props.items;
            selected = 0;
            onPick = item => props.command(item);
            root = document.createElement('div');
            root.className = 'cr-popup';
            document.body.append(root);
            draw();
            place(props.clientRect?.() ?? null);
        },
        onUpdate(props: SuggestionProps<RefSymbol>): void {
            items = props.items;
            selected = Math.min(selected, Math.max(0, items.length - 1));
            onPick = item => props.command(item);
            draw();
            place(props.clientRect?.() ?? null);
        },
        onKeyDown(props: SuggestionKeyDownProps): boolean {
            const { key } = props.event;
            if (key === 'ArrowDown') { selected = (selected + 1) % Math.max(1, items.length); draw(); return true; }
            if (key === 'ArrowUp') { selected = (selected - 1 + items.length) % Math.max(1, items.length); draw(); return true; }
            if (key === 'Enter' || key === 'Tab') {
                if (items[selected]) onPick?.(items[selected]);
                return true;
            }
            if (key === 'Escape') { exit(); return true; }
            return false;
        },
        onExit: exit,
    };
}

export const CodeRefSuggestion = Extension.create<CodeRefSuggestionOptions>({
    name: 'codeRefSuggestion',

    addOptions() {
        return { getSymbols: () => [], char: '@' };
    },

    addProseMirrorPlugins() {
        const options = this.options;
        return [
            Suggestion<RefSymbol>({
                editor: this.editor,
                char: options.char,
                pluginKey: suggestionKey,
                allowSpaces: false,
                items: ({ query }) => {
                    const q = query.toLowerCase();
                    const all = options.getSymbols();
                    const matched = q
                        ? all.filter(s => s.label.toLowerCase().includes(q) || s.file.toLowerCase().includes(q))
                        : all;
                    return matched.slice(0, 25);
                },
                command: ({ editor, range, props }) => {
                    editor
                        .chain()
                        .focus()
                        .insertContentAt(range, [
                            { type: 'codeRef', attrs: { label: props.label, file: props.file, symbol: props.symbol } },
                            { type: 'text', text: ' ' },
                        ])
                        .run();
                },
                render: makePopup,
            }),
        ];
    },
});
