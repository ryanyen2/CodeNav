// Commands on the page, with a button that copies them.
//
// Selecting the text and pressing Cmd+C works, and used to be all there was.
// But a participant mistyping one character of their own code files the session
// under nothing, and mistyping a path points the daemon at a folder that does
// not exist. Neither failure says anything at the time.

import { esc } from '../shared/html.js';

/** A command, with a copy button beside it. */
export function cmd(text) {
    return `<span class="cmd"><code class="pick">${esc(text)}</code>`
        + `<button type="button" class="copy" data-copy="${esc(text)}"`
        + ` aria-label="Copy ${esc(text)}">Copy</button></span>`;
}

/**
 * A block of text with a copy button, for something longer than a command.
 *
 * The request a participant sends their agent is a paragraph, and it has to
 * arrive exactly as written or two participants are reviewing answers to two
 * different questions. So it is copied rather than retyped.
 */
export function block(text, label = 'Copy') {
    return `<div class="paste"><pre class="pick">${esc(text)}</pre>`
        + `<button type="button" class="copy" data-copy="${esc(text)}"`
        + ` aria-label="${esc(label)}">${esc(label)}</button></div>`;
}

/**
 * Make every copy button inside `root` work.
 *
 * Called after each render, because the step's markup is replaced wholesale and
 * the old buttons go with it.
 */
export function wireCopy(root) {
    for (const b of root.querySelectorAll('button.copy')) {
        b.addEventListener('click', () => { void copyFrom(b); });
    }
}

async function copyFrom(button) {
    const text = button.dataset.copy;
    try {
        await navigator.clipboard.writeText(text);
    } catch {
        // The clipboard needs a permission the browser can refuse, and a dead
        // button that says "Copied" is worse than one that says what to do
        // instead. Select the text so the fallback is one keystroke.
        select(button.previousElementSibling);
        say(button, 'Press Cmd+C', 2400);
        return;
    }
    say(button, 'Copied', 1600);
}

function select(node) {
    if (!node || !node.ownerDocument) return;
    const win = node.ownerDocument.defaultView;
    const range = node.ownerDocument.createRange();
    range.selectNodeContents(node);
    const sel = win.getSelection();
    if (!sel) return;
    sel.removeAllRanges();
    sel.addRange(range);
}

function say(button, text, ms) {
    button.textContent = text;
    button.classList.add('done');
    clearTimeout(button._revert);
    button._revert = setTimeout(() => {
        button.textContent = 'Copy';
        button.classList.remove('done');
    }, ms);
}
