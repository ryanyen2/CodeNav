/**
 * find-view.ts — the ⌘F find & replace widget.
 *
 * Deliberately shaped like the editor find bar every VS Code user already knows:
 * a query field with Aa / ab / .* toggles, the match counter, ↑ ↓ to step, ✕ to
 * close, and a replace row behind a disclosure. Familiarity is the whole design
 * goal here — this is the affordance whose ABSENCE was the problem, so it should
 * cost nobody a moment's learning.
 *
 * It owns no search logic (that is `find.ts`) and no editor knowledge: it reports
 * intent through callbacks and renders whatever `FindState` comes back. It is not
 * modal — unlike the ⌘K palette there is no scrim, because searching while
 * reading the document is the entire point.
 */
import { DEFAULT_FIND_OPTIONS, matchLabel, type FindOptions, type FindState } from './find';

export interface FindViewHandle {
    element: HTMLElement;
    /** Show the widget, optionally with the replace row open, seeded with `seed`. */
    open: (opts?: { replace?: boolean; seed?: string }) => void;
    close: () => void;
    readonly isOpen: boolean;
    /** Render a state the caller computed (after a step / replace / doc change). */
    render: (state: FindState) => void;
    /** Keys the widget claims while open. Returns true when it consumed the event. */
    onKeydown: (ev: KeyboardEvent) => boolean;
    destroy: () => void;
}

export interface FindViewOptions {
    onSearch: (query: string, opts: FindOptions) => FindState;
    onStep: (delta: number) => FindState;
    onReplace: (replacement: string, preserveCase: boolean) => FindState;
    onReplaceAll: (replacement: string, preserveCase: boolean) => number;
    onClose: () => void;
}

function toggle(label: string, title: string, onChange: () => void): HTMLButtonElement {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ce-find-toggle';
    b.textContent = label;
    b.title = title;
    b.setAttribute('aria-pressed', 'false');
    b.addEventListener('mousedown', ev => ev.preventDefault());
    b.addEventListener('click', ev => {
        ev.preventDefault();
        b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
        b.classList.toggle('on', b.getAttribute('aria-pressed') === 'true');
        onChange();
    });
    return b;
}

function action(label: string, title: string, cls: string, onClick: () => void): HTMLButtonElement {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `ce-find-btn ${cls}`.trim();
    b.textContent = label;
    b.title = title;
    b.addEventListener('mousedown', ev => ev.preventDefault());
    b.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return b;
}

export function createFindView(opts: FindViewOptions): FindViewHandle {
    let open = false;

    const root = document.createElement('div');
    root.className = 'ce-find';
    root.hidden = true;

    // The disclosure that shows/hides the replace row — the same left-edge chevron
    // VS Code puts there, so the replace row is one click from find and no further.
    const expand = document.createElement('button');
    expand.type = 'button';
    expand.className = 'ce-find-expand';
    expand.textContent = '⌄';
    expand.title = 'Toggle Replace';
    expand.addEventListener('mousedown', ev => ev.preventDefault());
    expand.addEventListener('click', ev => { ev.preventDefault(); setReplaceOpen(!replaceOpen); });

    const rows = document.createElement('div');
    rows.className = 'ce-find-rows';

    // ── find row ──
    const findRow = document.createElement('div');
    findRow.className = 'ce-find-row';
    const queryWrap = document.createElement('div');
    queryWrap.className = 'ce-find-field';
    const query = document.createElement('input');
    query.type = 'text';
    query.className = 'ce-find-input';
    query.placeholder = 'Find';
    query.spellcheck = false;
    const caseBtn = toggle('Aa', 'Match Case', () => search());
    const wordBtn = toggle('ab', 'Match Whole Word', () => search());
    const reBtn = toggle('.*', 'Use Regular Expression', () => search());
    queryWrap.append(query, caseBtn, wordBtn, reBtn);

    const count = document.createElement('span');
    count.className = 'ce-find-count';
    count.textContent = 'No results';

    findRow.append(
        queryWrap,
        count,
        action('↑', 'Previous Match (⇧↵)', 'ce-find-prev', () => render(opts.onStep(-1))),
        action('↓', 'Next Match (↵)', 'ce-find-next', () => render(opts.onStep(+1))),
        action('✕', 'Close (Esc)', 'ce-find-close', () => close()),
    );

    // ── replace row ──
    const replaceRow = document.createElement('div');
    replaceRow.className = 'ce-find-row ce-find-replace-row';
    const replaceWrap = document.createElement('div');
    replaceWrap.className = 'ce-find-field';
    const replace = document.createElement('input');
    replace.type = 'text';
    replace.className = 'ce-find-input';
    replace.placeholder = 'Replace';
    replace.spellcheck = false;
    const preserveBtn = toggle('AB', 'Preserve Case', () => undefined);
    replaceWrap.append(replace, preserveBtn);
    replaceRow.append(
        replaceWrap,
        action('⇥', 'Replace (⌘⌥↵)', 'ce-find-do', () => doReplace()),
        action('⇥⇥', 'Replace All (⌘⌥⏎)', 'ce-find-do-all', () => doReplaceAll()),
    );

    rows.append(findRow, replaceRow);
    root.append(expand, rows);

    let replaceOpen = false;
    function setReplaceOpen(on: boolean): void {
        replaceOpen = on;
        root.classList.toggle('with-replace', on);
        replaceRow.hidden = !on;
        expand.textContent = on ? '⌃' : '⌄';
    }
    setReplaceOpen(false);

    function options(): FindOptions {
        return {
            ...DEFAULT_FIND_OPTIONS,
            caseSensitive: caseBtn.classList.contains('on'),
            wholeWord: wordBtn.classList.contains('on'),
            regex: reBtn.classList.contains('on'),
        };
    }

    function render(state: FindState): void {
        count.textContent = matchLabel(state.index, state.count);
        // The empty query is not "no results" — it is "nothing asked yet", and
        // colouring the field red for it would scold the reader for starting to type.
        const barren = state.query.length > 0 && state.count === 0;
        count.classList.toggle('empty', barren);
        query.classList.toggle('no-match', barren);
    }

    function search(): void {
        render(opts.onSearch(query.value, options()));
    }

    function doReplace(): void {
        render(opts.onReplace(replace.value, preserveBtn.classList.contains('on')));
    }

    function doReplaceAll(): void {
        const n = opts.onReplaceAll(replace.value, preserveBtn.classList.contains('on'));
        // Re-search rather than trusting a count: after a bulk edit the only honest
        // state is what the document now actually contains.
        search();
        count.textContent = n ? `Replaced ${n}` : 'No results';
    }

    query.addEventListener('input', () => search());

    function close(): void {
        if (!open) return;
        open = false;
        root.hidden = true;
        opts.onClose();
    }

    return {
        element: root,
        get isOpen() { return open; },
        open: (o = {}) => {
            const wasOpen = open;
            open = true;
            root.hidden = false;
            if (o.replace) setReplaceOpen(true);
            // Seed from the selection, the way ⌘F does everywhere — but never
            // clobber a query already being worked on by re-opening over it.
            if (o.seed && (!wasOpen || !query.value)) query.value = o.seed;
            query.focus();
            query.select();
            if (query.value) search();
        },
        close,
        render,
        onKeydown: (ev: KeyboardEvent): boolean => {
            if (!open) return false;
            const inWidget = root.contains(document.activeElement);
            if (ev.key === 'Escape') {
                // Escape always closes, from anywhere — it is the one key a reader
                // reaches for when a widget is in the way.
                ev.preventDefault();
                close();
                return true;
            }
            if (!inWidget) return false;
            if (ev.key === 'Enter') {
                ev.preventDefault();
                if ((ev.metaKey || ev.ctrlKey) && ev.altKey) { doReplaceAll(); return true; }
                if (ev.altKey && document.activeElement === replace) { doReplace(); return true; }
                render(opts.onStep(ev.shiftKey ? -1 : +1));
                return true;
            }
            if (ev.key === 'Tab' && !ev.shiftKey && document.activeElement === query && replaceOpen) {
                ev.preventDefault();
                replace.focus();
                replace.select();
                return true;
            }
            return false;
        },
        destroy: () => root.remove(),
    };
}
