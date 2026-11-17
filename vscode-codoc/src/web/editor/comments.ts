/**
 * comments.ts — comment threads as kind blocks (03-extensions §4, 09-editor §7).
 *
 * A comment is a block of kind `comment` (one message per block, `thread`
 * shared by replies) anchored by a `comment{block}` mark on the words it
 * discusses. It is out of the reading flow: the editor never renders it as a
 * node; it draws a card in the margin beside the anchor block. Everything a
 * card does is ordinary ops by the person: a new thread is `block.add` +
 * `text.insert` (the body) + `text.mark(comment)`; a reply is another block;
 * Resolve is `block.set(status)`; Build it is a hand-off of the anchor's
 * feature with the comment as what the author asked.
 */
import type { EditorView } from '@tiptap/pm/view';

import { START, textField, type OpBody, type Projection } from '../calc/projection';

interface Message { bid: string; actor: string; at: string; body: string }
export interface Thread {
    id: string;
    anchor: string;          // the block the mark sits in
    anchorText: string;
    scope: 'code' | 'both';
    status: 'open' | 'resolved';
    messages: Message[];
}

const COMMENT = 'comment';

/** Everything a card draws. A card whose key is unchanged is the same card and
 *  is left alone, with whatever its reader typed into it. */
function threadKey(t: Thread): string {
    return [t.anchor, t.anchorText, t.scope, t.status, t.messages.length,
            t.messages.map(m => `${m.actor}\u0000${m.body}`).join('\u0001')].join('|');
}

/** Where each thread's words are now: the block holding its `comment` mark.
 *  The `anchor` field is the hint the thread was written with; the mark is the
 *  anchor of record, and it travels with the words through a split or a merge. */
export function anchorsOf(proj: Projection): Map<string, string> {
    const out = new Map<string, string>();
    for (const b of proj.blocks) {
        const f = textField(b);
        for (const s of f?.spans ?? []) {
            if (s.kind === COMMENT && s.attrs.block) out.set(s.attrs.block, b.id);
        }
    }
    return out;
}

/** Threads from the projection's comment blocks, in document order. */
export function threadsOf(proj: Projection): Thread[] {
    const live = anchorsOf(proj);
    const out = new Map<string, Thread>();
    for (const b of proj.blocks) {
        if (b.kind !== COMMENT) continue;
        const f = b.fields as Record<string, unknown>;
        const id = String(f.thread ?? b.id);
        const body = textField(b, 'body');
        const first = body?.runs[0];
        const msg: Message = { bid: b.id, actor: first?.actor ?? '', at: first?.at ?? '', body: body?.text ?? '' };
        const t = out.get(id);
        if (t) { t.messages.push(msg); continue; }
        out.set(id, { id, anchor: live.get(id) ?? String(f.anchor ?? ''), anchorText: String(f.anchor_text ?? ''),
                      scope: f.scope === 'both' ? 'both' : 'code', status: f.status === 'resolved' ? 'resolved' : 'open', messages: [msg] });
    }
    return [...out.values()];
}

/** The ops for a new thread on `anchor`'s words `[start, end]` (character refs). */
export function newThreadOps(bid: string, anchor: string, anchorText: string, body: string, scope: 'code' | 'both',
                             start: unknown, end: unknown): OpBody[] {
    return [
        { kind: 'block.add', target: { bid }, payload: { kind: COMMENT, after: anchor,
          content: { thread: bid, anchor, anchor_text: anchorText, scope, status: 'open' } } },
        { kind: 'text.insert', target: { bid, path: 'body' }, payload: { after: START, text: body } },
        { kind: 'text.mark', target: { bid: anchor, path: 'text' }, payload: { kind: COMMENT, attrs: { block: bid }, start, end, on: true } },
    ];
}

function replyOps(t: Thread, bid: string, body: string): OpBody[] {
    const last = t.messages[t.messages.length - 1]?.bid ?? t.anchor;
    return [
        { kind: 'block.add', target: { bid }, payload: { kind: COMMENT, after: last,
          content: { thread: t.id, anchor: t.anchor, anchor_text: t.anchorText, scope: t.scope, status: t.status } } },
        { kind: 'text.insert', target: { bid, path: 'body' }, payload: { after: START, text: body } },
    ];
}

export function resolveOps(t: Thread): OpBody[] {
    return t.messages.map(m => ({ kind: 'block.set', target: { bid: m.bid, path: 'status' }, payload: { value: 'resolved' } }));
}

export interface MarginHost {
    submit(ops: OpBody[]): void;
    /** Hand the anchor's feature off with the comment as the request, then build. */
    build(thread: Thread): void;
    newBid(): string;
    featureOf(bid: string): string | null;
    /** The viewer, and whether they answer for the tree. A contributor may
     *  resolve only a thread they started — the applier refuses the rest, and a
     *  button whose refusal reloads the card is worse than no button. */
    me?(): { actor: string; decides: boolean };
}

/** The margin: one card per open thread, hung beside its anchor block. */
export class Margin {
    readonly el: HTMLElement;
    private threads: Thread[] = [];
    private draft: { anchor: string; anchorText: string; start: unknown; end: unknown } | null = null;

    constructor(private readonly page: HTMLElement, private readonly view: () => EditorView | null, private readonly host: MarginHost) {
        this.el = document.createElement('aside');
        this.el.className = 'codoc-margin';
        page.append(this.el);
        window.addEventListener('resize', () => this.layout());
    }

    update(proj: Projection): void {
        this.threads = threadsOf(proj).filter(t => t.status === 'open');
        this.render();
    }

    /** Start a thread on the current selection (the app passes the anchor refs). */
    compose(anchor: string, anchorText: string, start: unknown, end: unknown): void {
        this.draft = { anchor, anchorText, start, end };
        this.render();
        (this.el.querySelector('.codoc-card.is-draft textarea') as HTMLTextAreaElement | null)?.focus();
    }

    /** One element per thread, kept across updates and keyed by its id. Only a
     *  card whose thread changed is rebuilt. A card that is never replaced
     *  keeps what its reader typed into it, the focus and the caret, none of
     *  which survive a rebuild because a textarea's value lives only in the DOM. */
    private cards = new Map<string, { el: HTMLElement; key: string }>();

    private render(): void {
        const want = new Map<string, Thread | null>();
        if (this.draft) want.set('draft', null);
        for (const t of this.threads) want.set(t.id, t);
        for (const [id, held] of this.cards) {
            if (!want.has(id)) { held.el.remove(); this.cards.delete(id); }
        }
        for (const [id, t] of want) {
            const key = id === 'draft' ? `draft:${this.draft?.anchor}:${this.draft?.anchorText}` : threadKey(t!);
            const held = this.cards.get(id);
            if (held && held.key === key) { this.el.append(held.el); continue; }   // append = move into order
            const el = this.card(t, t ? null : this.draft);
            held?.el.replaceWith(el);
            if (!held) this.el.append(el);
            this.cards.set(id, { el, key });
        }
        this.layout();
    }

    private card(t: Thread | null, draft: Margin['draft']): HTMLElement {
        const anchor = t?.anchor ?? draft!.anchor;
        const card = document.createElement('div');
        card.className = `codoc-card${t ? '' : ' is-draft'}`;
        card.dataset.anchor = anchor;
        if (t) card.dataset.thread = t.id;
        const quote = document.createElement('div');
        quote.className = 'codoc-card-quote';
        quote.textContent = t?.anchorText ?? draft!.anchorText;
        card.append(quote);
        for (const m of t?.messages ?? []) {
            const row = document.createElement('div');
            row.className = 'codoc-card-msg';
            const who = document.createElement('span');
            who.className = 'codoc-card-who';
            who.textContent = m.actor.replace(/^human:/, '').replace(/^agent:/, '') || '…';
            const body = document.createElement('span');
            body.textContent = m.body;
            row.append(who, body);
            card.append(row);
        }
        const ta = document.createElement('textarea');
        ta.className = 'codoc-card-input';
        ta.placeholder = t ? 'Reply' : 'What should change here?';
        ta.rows = 2;
        card.append(ta);
        const actions = document.createElement('div');
        actions.className = 'codoc-card-actions';
        const post = (scope: 'code' | 'both') => {
            const body = ta.value.trim();
            if (!body) return;
            if (t) this.host.submit(replyOps(t, this.host.newBid(), body));
            else { const d = draft!; this.host.submit(newThreadOps(this.host.newBid(), d.anchor, d.anchorText, body, scope, d.start, d.end)); this.draft = null; }
            ta.value = '';
            this.render();
        };
        if (t) {
            const me = this.host.me?.() ?? { actor: '', decides: true };
            btn(actions, 'Reply', () => post(t.scope));
            if (me.decides || t.messages[0]?.actor === me.actor) btn(actions, 'Resolve', () => { this.host.submit(resolveOps(t)); });
            if (t.scope === 'code' || t.scope === 'both') btn(actions, 'Build it', () => this.host.build(t), 'codoc-btn-human');
        } else {
            btn(actions, 'Comment', () => post('code'));
            btn(actions, 'Comment + prose', () => post('both'), '', 'also bring the description in line');
            btn(actions, 'Cancel', () => { this.draft = null; this.render(); });
        }
        card.append(actions);
        card.addEventListener('mouseenter', () => this.light(anchor, true));
        card.addEventListener('mouseleave', () => this.light(anchor, false));
        return card;
    }

    private light(anchor: string, on: boolean): void {
        this.view()?.dom.querySelector(`[data-bid="${anchor}"]`)?.classList.toggle('is-commented', on);
    }

    /** Hang each card at its anchor's top; stack the ones that would overlap. */
    layout(): void {
        const v = this.view();
        if (!v) return;
        const pageTop = this.page.getBoundingClientRect().top - this.page.scrollTop;
        let floor = 0;
        for (const card of Array.from(this.el.children) as HTMLElement[]) {
            const a = v.dom.querySelector(`[data-bid="${card.dataset.anchor}"]`) as HTMLElement | null;
            const top = a ? a.getBoundingClientRect().top - pageTop : floor;
            const y = Math.max(top, floor);
            card.style.top = `${y}px`;
            floor = y + card.offsetHeight + 8;
        }
    }
}

function btn(parent: HTMLElement, label: string, onClick: () => void, cls = '', title = ''): void {
    const b = document.createElement('button');
    b.className = `codoc-btn ${cls}`.trim();
    b.textContent = label;
    if (title) b.title = title;
    b.addEventListener('click', onClick);
    parent.append(b);
}
