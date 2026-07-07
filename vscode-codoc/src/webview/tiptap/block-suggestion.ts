/**
 * block-suggestion.ts — `/`-triggered menu to author a typed-media block (Phase 0
 * of the blocks-feature debug pass: the "/" menu existed as pure, unit-tested
 * logic in block-slash-menu.ts but was never wired into the live editor, so
 * there was previously no way to create a block through the UI at all).
 *
 * Typing `/` at the start of an empty line inside a feature's description opens
 * a filtered popup over block-slash-menu.ts's kind catalog (mirrors
 * code-ref-suggestion.ts's `@`-mention popup). Blocks are NOT ProseMirror doc
 * nodes — they live in the separate `payload.blocks` side-channel rendered by
 * block-decorations.ts — so picking a kind never inserts document content; it
 * (1) removes the `/query` trigger text and (2) hands a `block-edit`
 * (action:'add') to the host, which the daemon's next Loop A/B pass turns into
 * a real, persisted block (the same "authoring is a host action, content fills
 * in later" precedent an existing diagram already follows).
 *
 * image/pdf need a file's bytes, not typed text, so picking one opens a native
 * file dialog instead (mirrors the comment composer's screenshot-attach flow,
 * whole-doc-editor.ts) — cancelling it creates nothing (no orphan block).
 */
import { Extension } from '@tiptap/core';
import Suggestion, { SuggestionKeyDownProps, SuggestionProps } from '@tiptap/suggestion';
import { PluginKey } from '@tiptap/pm/state';
import { filterBlockKinds, mintBlockId, type BlockKindItem } from './block-slash-menu';
import type { BlockEditMsg } from './block-decorations';

const suggestionKey = new PluginKey('codocBlockSuggestion');

// Kinds that need a file's bytes rather than typed text — picking one opens a
// native file dialog (the `accept` filter) instead of an empty block to type into.
const FILE_KINDS: Record<string, string> = { image: 'image/*', pdf: 'application/pdf' };

/** A tiny keyboard-navigable popup, positioned at the caret — the same vanilla-DOM
 *  shape as code-ref-suggestion.ts's `makePopup`, over block kinds instead of
 *  code symbols (no icons: BlockKindItem's `glyph` doesn't map to icons.ts's
 *  fixed lifecycle-glyph registry, and the sibling `@`-mention popup is
 *  text-only too, so this stays consistent with it rather than fighting that). */
function makePopup() {
    let root: HTMLElement | null = null;
    let items: BlockKindItem[] = [];
    let selected = 0;
    let onPick: ((item: BlockKindItem) => void) | null = null;

    const draw = (): void => {
        if (!root) return;
        root.replaceChildren();
        if (items.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'cr-empty';
            empty.textContent = 'No matching block';
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
            det.textContent = it.hint;
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
        onStart(props: SuggestionProps<BlockKindItem>): void {
            items = props.items;
            selected = 0;
            onPick = item => props.command(item);
            root = document.createElement('div');
            root.className = 'cr-popup ce-block-menu';
            document.body.append(root);
            draw();
            place(props.clientRect?.() ?? null);
        },
        onUpdate(props: SuggestionProps<BlockKindItem>): void {
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

/** One hidden file input, lazily created + reused across picks (`accept` is set
 *  per-kind before each `.click()`) — the same base64-via-FileReader pattern as
 *  the comment composer's screenshot attach (whole-doc-editor.ts), which is the
 *  only attachment path that works identically in BOTH hosts (the hub is a plain
 *  browser tab with no VS Code `showOpenDialog`). Resolves `null` if the user
 *  cancels (no `change` event) — the caller must create nothing in that case. */
let fileInput: HTMLInputElement | null = null;
function pickFile(accept: string): Promise<{ data: string; mime: string } | null> {
    if (!fileInput) {
        fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.style.display = 'none';
        document.body.append(fileInput);
    }
    const input = fileInput;
    input.accept = accept;
    input.value = '';
    return new Promise(resolve => {
        const onChange = (): void => {
            input.removeEventListener('change', onChange);
            const f = input.files && input.files[0];
            if (!f) { resolve(null); return; }
            const reader = new FileReader();
            reader.onload = () => {
                const res = String(reader.result || '');
                const comma = res.indexOf(',');
                resolve({ data: comma >= 0 ? res.slice(comma + 1) : res, mime: f.type || accept });
            };
            reader.onerror = () => resolve(null);
            reader.readAsDataURL(f);
        };
        input.addEventListener('change', onChange);
        input.click();
    });
}

export interface BlockSuggestionOptions {
    /** The fid owning the caret's current position, or null if none — a block
     *  can't be created with no owning feature. */
    getActiveFid: () => string | null;
    onCreate: (edit: BlockEditMsg) => void;
    char: string;
}

export const BlockSuggestion = Extension.create<BlockSuggestionOptions>({
    name: 'blockSuggestion',

    addOptions() {
        return { getActiveFid: () => null, onCreate: () => {}, char: '/' };
    },

    addProseMirrorPlugins() {
        const options = this.options;
        return [
            Suggestion<BlockKindItem>({
                editor: this.editor,
                char: options.char,
                pluginKey: suggestionKey,
                startOfLine: true,
                allowSpaces: false,
                // Only inside a feature's description (not its heading — a block
                // can't attach to a title) and only when a feature actually owns
                // the caret (no orphan blocks with no feature_id to attach to).
                allow: ({ editor }) => {
                    const { $from } = editor.state.selection;
                    return $from.parent.type.name !== 'featureHeading' && !!options.getActiveFid();
                },
                items: ({ query }) => filterBlockKinds(query).slice(0, 10),
                command: ({ editor, range, props }) => {
                    const fid = options.getActiveFid();
                    // The `/query` trigger text is never real content — remove it
                    // regardless of what happens next (including a cancelled file pick).
                    editor.chain().focus().deleteRange(range).run();
                    if (!fid) return;
                    const kind = props.kind;
                    const accept = FILE_KINDS[kind];
                    if (accept) {
                        void pickFile(accept).then(file => {
                            if (!file) return; // cancelled — create nothing
                            options.onCreate({
                                block_id: mintBlockId(), feature_id: fid, kind,
                                action: 'add', content: '', prev_content: '',
                                mediaData: file.data, mediaMime: file.mime,
                            });
                        });
                        return;
                    }
                    options.onCreate({
                        block_id: mintBlockId(), feature_id: fid, kind,
                        action: 'add', content: '', prev_content: '',
                    });
                },
                render: makePopup,
            }),
        ];
    },
});
