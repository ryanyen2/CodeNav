/**
 * block-decorations.ts — render typed-media blocks (v6) in the doc pane.
 *
 * A feature can own typed-media blocks beyond its prose (diagram / image / latex /
 * url). The host forwards them as `payload.blocks` (per feature, ordered, persistent
 * only — transient blocks ride the steers channel and never reach here). This plugin
 * renders each as a calm widget anchored AFTER the feature heading, by `kind`.
 *
 * Two block-model rules show up directly in the UI:
 *  - **Stable id (KTD8):** every widget carries `data-block-id`; an edit posts that id
 *    so identity is never inferred from content. A move would be an `ord` change with
 *    the same id — the host emits no block-edit for it, so reordering is free.
 *  - **Capability honesty (KTD5):** only text-content, lower-capable kinds (diagram,
 *    latex) get an editable surface; consult-only media (url, image) render read-only.
 *    An unknown kind degrades to an inert placeholder rather than breaking the doc.
 *
 * Editing a diagram/latex block blurs → posts a `block-edit` (action `edit`) with the
 * prior + new content; Loop B's `lower` dispatch turns the delta into a scoped
 * directive (a diagram edge add/remove → a code change). Read-only by default.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { UIBlock } from '../protocol';

export const BLOCKS_UPDATED = 'codocBlocksUpdated';
const blockKey = new PluginKey('codocBlockDecorations');

/** A block-edit the webview hands back to the host (→ edits.json → Loop B `lower`). */
export interface BlockEditMsg {
    block_id: string;
    feature_id: string;
    kind: string;
    action: 'edit' | 'add' | 'remove';
    content: string;
    prev_content: string;
}

export interface BlockDecorationsOptions {
    /** Per-feature blocks (the host's `payload.blocks`). */
    getBlocks: () => Record<string, UIBlock[]>;
    /** Hand a content edit / removal back to the host. Omitted ⇒ read-only (tests). */
    onEdit?: (edit: BlockEditMsg) => void;
}

// Text-content kinds whose content is directly editable as plain text and whose
// plugin declares LOWER (an edit implies a code change). Consult-only media
// (url/image) and unknown kinds render read-only.
export const EDITABLE_KINDS = new Set(['diagram', 'latex']);

/** Construct the block-edit message for a content change, or null when unchanged.
 *  Pure (no DOM) so the edit round-trip logic is unit-testable headlessly: an edit
 *  carries the stable id (KTD8) + prev/new content for Loop B's `lower` delta. */
export function blockEditMsg(block: UIBlock, fid: string, newContent: string): BlockEditMsg | null {
    if (newContent === block.content) return null;  // no-op (e.g. edited then reverted)
    return {
        block_id: block.id, feature_id: fid, kind: block.kind,
        action: 'edit', content: newContent, prev_content: block.content,
    };
}

function renderBlock(block: UIBlock, fid: string, onEdit?: (e: BlockEditMsg) => void): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = `ce-block ce-block-${block.kind}`;
    wrap.contentEditable = 'false';
    wrap.setAttribute('data-block-id', block.id);     // stable id (KTD8)
    wrap.setAttribute('data-block-kind', block.kind);

    const label = document.createElement('span');
    label.className = 'ce-block-kind';
    label.textContent = block.kind + (block.provenance === 'derived' ? ' · derived' : '');
    wrap.append(label);

    if (block.kind === 'url') {
        const a = document.createElement('a');
        a.className = 'ce-block-url';
        a.textContent = block.content;
        a.href = block.content;
        wrap.append(a);
    } else if (block.kind === 'image') {
        const ref = document.createElement('span');
        ref.className = 'ce-block-image';
        ref.textContent = block.content;
        wrap.append(ref);
    } else if (EDITABLE_KINDS.has(block.kind)) {
        const pre = document.createElement('pre');
        pre.className = 'ce-block-content';
        pre.textContent = block.content;
        if (onEdit) {
            pre.contentEditable = 'true';
            pre.addEventListener('blur', () => {
                const msg = blockEditMsg(block, fid, pre.textContent ?? '');
                if (msg) onEdit(msg);
            });
        }
        wrap.append(pre);
    } else {
        // Unknown kind: inert placeholder — the doc never breaks on a medium this
        // host doesn't know how to render (forward-compat with new plugins).
        const ph = document.createElement('span');
        ph.className = 'ce-block-unknown';
        ph.textContent = `(unsupported block: ${block.kind})`;
        wrap.append(ph);
    }
    return wrap;
}

/** Build one widget per feature carrying its blocks, anchored after the heading.
 *  Exported for headless tests (the widget DOM factory only runs when the view renders). */
export function buildBlockDecorations(
    doc: PMModelNode,
    blocksByFid: Record<string, UIBlock[]>,
    onEdit?: (e: BlockEditMsg) => void,
): DecorationSet {
    if (!Object.keys(blocksByFid).length) return DecorationSet.empty;
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const blocks = blocksByFid[fid];
        if (!blocks || !blocks.length) return;
        decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
            const container = document.createElement('div');
            container.className = 'ce-blocks';
            container.contentEditable = 'false';
            // ord is already applied host-side; render in array order. fid is passed
            // explicitly so an edit can name the feature (the host slice keys by it).
            for (const b of blocks) container.append(renderBlock(b, fid, onEdit));
            return container;
        }, { side: 1, key: 'blocks-' + fid }));
    });
    return DecorationSet.create(doc, decos);
}

export const BlockDecorations = Extension.create<BlockDecorationsOptions>({
    name: 'blockDecorations',

    addOptions() {
        return { getBlocks: () => ({}) };
    },

    addProseMirrorPlugins() {
        const getBlocks = (): Record<string, UIBlock[]> => this.options.getBlocks();
        const onEdit = this.options.onEdit;
        return [
            new Plugin({
                key: blockKey,
                state: {
                    init: (_c, state) => buildBlockDecorations(state.doc, getBlocks(), onEdit),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(BLOCKS_UPDATED) || tr.docChanged) {
                            return buildBlockDecorations(newState.doc, getBlocks(), onEdit);
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return blockKey.getState(state); } },
            }),
        ];
    },
});
