/**
 * schema.ts — the document vocabulary of the editor (09-editor §2).
 *
 *   doc    := (heading | paragraph | kindBlock)+
 *   inline := text | hardBreak
 *   marks  := bold | link{href} | cite{file, symbol} | comment{block} | suggest_del{plan} | plan{pid}
 *
 * A block node carries two attributes from the projection: `bid`, its
 * identity, and `head`, the store position of the last op that touched it,
 * which is what adoption compares. Who wrote a word and which plan it belongs
 * to are not in the document. They are read from the projection's runs and
 * drawn as decorations.
 *
 * A person sets `bold`, `link` and `cite`, and the recorder sends them. The
 * projection sets `plan`, `suggest_del` and `comment`; the recorder never
 * sends those, and `plan` and `suggest_del` are non-inclusive so that typing
 * at their edge is the person's own text.
 *
 * Any other block in the reading flow is a `kindBlock`: an atom drawn
 * read-only by `kindView` from the fields the projection gives it. Comment
 * blocks are not in the flow; they are cards in the margin (comments.ts).
 */
import { Mark, Node, getSchema, mergeAttributes } from '@tiptap/core';
import Document from '@tiptap/extension-document';
import HardBreak from '@tiptap/extension-hard-break';
import Text from '@tiptap/extension-text';
import type { Node as PMNode } from '@tiptap/pm/model';

export const MAX_LEVEL = 6;

function idAttrs() {
    return {
        bid: {
            default: null,
            parseHTML: (el: HTMLElement) => el.getAttribute('data-bid'),
            renderHTML: (attrs: Record<string, unknown>) => (attrs.bid ? { 'data-bid': String(attrs.bid) } : {}),
        },
        head: {
            default: 0,
            parseHTML: (el: HTMLElement) => Number(el.getAttribute('data-head') || 0),
            renderHTML: (attrs: Record<string, unknown>) => ({ 'data-head': String(attrs.head ?? 0) }),
        },
    };
}

const Heading = Node.create({
    name: 'heading',
    group: 'block',
    content: 'inline*',
    defining: true,
    addAttributes() {
        return {
            ...idAttrs(),
            level: {
                default: 1,
                parseHTML: (el: HTMLElement) => Math.min(MAX_LEVEL, Math.max(1, Number(el.getAttribute('data-level') || 1))),
                renderHTML: (attrs: Record<string, unknown>) => ({ 'data-level': String(attrs.level ?? 1) }),
            },
        };
    },
    parseHTML() {
        return [{ tag: 'div[data-heading]' }];
    },
    renderHTML({ node, HTMLAttributes }) {
        return ['div', mergeAttributes(HTMLAttributes, { 'data-heading': '', class: `codoc-heading codoc-h${node.attrs.level}` }), 0];
    },
});

const Paragraph = Node.create({
    name: 'paragraph',
    group: 'block',
    content: 'inline*',
    addAttributes() {
        return idAttrs();
    },
    parseHTML() {
        return [{ tag: 'p' }];
    },
    renderHTML({ HTMLAttributes }) {
        return ['p', mergeAttributes(HTMLAttributes, { class: 'codoc-paragraph' }), 0];
    },
});

/** A block of any plugin kind (a diagram, an image). The editor authors no ops against it. */
const KindBlock = Node.create({
    name: 'kindBlock',
    group: 'block',
    atom: true,
    selectable: false,
    draggable: false,
    addAttributes() {
        return {
            ...idAttrs(),
            kind: { default: '', renderHTML: (attrs: Record<string, unknown>) => ({ 'data-kind': String(attrs.kind ?? '') }) },
            fields: { default: {}, renderHTML: () => ({}) },
        };
    },
    parseHTML() { return [{ tag: 'div[data-kind]' }]; },
    renderHTML({ node, HTMLAttributes }) {
        return ['div', mergeAttributes(HTMLAttributes, { class: 'codoc-kind', 'data-kind': node.attrs.kind }), 0];
    },
});

const Bold = Mark.create({
    name: 'bold',
    inclusive: true,
    parseHTML() { return [{ tag: 'strong' }]; },
    renderHTML({ HTMLAttributes }) { return ['strong', mergeAttributes(HTMLAttributes, { class: 'codoc-focus' }), 0]; },
});

const Link = Mark.create({
    name: 'link',
    inclusive: false,
    excludes: 'cite',
    addAttributes() {
        return { href: { default: '' } };
    },
    parseHTML() { return [{ tag: 'a[href]' }]; },
    renderHTML({ HTMLAttributes }) { return ['a', mergeAttributes(HTMLAttributes, { class: 'codoc-consult', rel: 'noopener' }), 0]; },
});

const Cite = Mark.create({
    name: 'cite',
    inclusive: false,
    excludes: 'link',
    addAttributes() {
        return { file: { default: '' }, symbol: { default: '' } };
    },
    parseHTML() { return [{ tag: 'span[data-cite]' }]; },
    renderHTML({ mark, HTMLAttributes }) {
        const sym = mark.attrs.symbol ? `#${mark.attrs.symbol}` : '';
        return ['span', mergeAttributes(HTMLAttributes, { 'data-cite': '', class: 'codoc-cite', title: `codoc:${mark.attrs.file}${sym}` }), 0];
    },
});

const Comment = Mark.create({
    name: 'comment',
    inclusive: false,
    addAttributes() {
        return { block: { default: '' } };
    },
    parseHTML() { return [{ tag: 'span[data-comment]' }]; },
    renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-comment': '', class: 'codoc-comment' }), 0]; },
});

const SuggestDel = Mark.create({
    name: 'suggest_del',
    inclusive: false,
    addAttributes() {
        return { plan: { default: '' } };
    },
    parseHTML() { return [{ tag: 'span[data-suggest-del]' }]; },
    renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-suggest-del': '', class: 'codoc-plan-cut' }), 0]; },
});

const Plan = Mark.create({
    name: 'plan',
    inclusive: false,
    addAttributes() {
        return { pid: { default: '' } };
    },
    parseHTML() { return [{ tag: 'span[data-plan]' }]; },
    renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-plan': '', class: 'codoc-plan' }), 0]; },
});

export function codocSchema() {
    return getSchema([Document, Text, HardBreak, Heading, Paragraph, KindBlock, Bold, Link, Cite, Comment, SuggestDel, Plan]);
}

/** The node view for a `kindBlock`: the kind's name, then one row per field
 *  that has a value. A Text field shows its words, anything else its JSON. */
export function kindView(node: PMNode): { dom: HTMLElement } {
    const dom = document.createElement('div');
    dom.className = `codoc-kind codoc-kind-${node.attrs.kind}`;
    dom.dataset.bid = node.attrs.bid;
    dom.dataset.kind = node.attrs.kind;
    dom.contentEditable = 'false';
    const label = document.createElement('div');
    label.className = 'codoc-kind-label';
    label.textContent = String(node.attrs.kind);
    dom.append(label);
    const fields = (node.attrs.fields ?? {}) as Record<string, unknown>;
    for (const [k, v] of Object.entries(fields)) {
        const row = document.createElement('div');
        row.className = 'codoc-kind-field';
        const isText = v && typeof v === 'object' && 'runs' in (v as object);
        const value = isText ? String((v as { text: string }).text) : typeof v === 'string' ? v : JSON.stringify(v);
        if (!value) continue;
        const key = document.createElement('span');
        key.className = 'codoc-kind-key';
        key.textContent = k;
        const val = document.createElement(isText && k === 'source' ? 'pre' : 'span');
        val.className = 'codoc-kind-value';
        val.textContent = value;
        row.append(key, val);
        dom.append(row);
    }
    return { dom };
}
