/**
 * schema.ts — the v2 editor's document vocabulary (09-editor §2).
 *
 *   doc := (heading | paragraph)*        heading(bid, level, head)  paragraph(bid, head)
 *   inline := text | hardBreak
 *   marks := bold | link{href} | cite{file, symbol} | comment{block} | suggest_del{plan} | plan{pid}
 *
 * `bid` is the only identity a node carries; `head` is the store position of
 * the last op that touched the block, the per-block adoption gate. No author
 * mark, no ownerId, no localId, no proposed/retired flags: attribution and plan
 * membership are read from the projection's runs and drawn as decorations.
 * `plan` and `suggest_del` are the two marks the projection paints and the
 * recorder never sends; both are non-inclusive so typing at their edge is
 * the person's (I2).
 */
import { Extensions, Mark, Node, getSchema, mergeAttributes } from '@tiptap/core';
import Document from '@tiptap/extension-document';
import HardBreak from '@tiptap/extension-hard-break';
import Text from '@tiptap/extension-text';

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

export const Heading = Node.create({
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

export const Paragraph = Node.create({
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

export const Bold = Mark.create({
    name: 'bold',
    inclusive: true,
    parseHTML() { return [{ tag: 'strong' }]; },
    renderHTML({ HTMLAttributes }) { return ['strong', mergeAttributes(HTMLAttributes, { class: 'codoc-focus' }), 0]; },
});

export const Link = Mark.create({
    name: 'link',
    inclusive: false,
    excludes: 'cite',
    addAttributes() {
        return { href: { default: '' } };
    },
    parseHTML() { return [{ tag: 'a[href]' }]; },
    renderHTML({ HTMLAttributes }) { return ['a', mergeAttributes(HTMLAttributes, { class: 'codoc-consult', rel: 'noopener' }), 0]; },
});

export const Cite = Mark.create({
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

export const Comment = Mark.create({
    name: 'comment',
    inclusive: false,
    addAttributes() {
        return { block: { default: '' } };
    },
    parseHTML() { return [{ tag: 'span[data-comment]' }]; },
    renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-comment': '', class: 'codoc-comment' }), 0]; },
});

export const SuggestDel = Mark.create({
    name: 'suggest_del',
    inclusive: false,
    addAttributes() {
        return { plan: { default: '' } };
    },
    parseHTML() { return [{ tag: 'span[data-suggest-del]' }]; },
    renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-suggest-del': '', class: 'codoc-plan-cut' }), 0]; },
});

export const Plan = Mark.create({
    name: 'plan',
    inclusive: false,
    addAttributes() {
        return { pid: { default: '' } };
    },
    parseHTML() { return [{ tag: 'span[data-plan]' }]; },
    renderHTML({ HTMLAttributes }) { return ['span', mergeAttributes(HTMLAttributes, { 'data-plan': '', class: 'codoc-plan' }), 0]; },
});

/** Marks the recorder sends as `text.mark`; the rest are painted by the projection. */
export const RECORDED_MARKS = new Set(['bold', 'link', 'cite']);

export function codocExtensions(): Extensions {
    return [Document, Text, HardBreak, Heading, Paragraph, Bold, Link, Cite, Comment, SuggestDel, Plan];
}

export function codocSchema() {
    return getSchema(codocExtensions());
}
