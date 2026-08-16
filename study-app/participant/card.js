// Draws a task card as a picture.
//
// The guide has always said to show the card as an image and never as text. If a
// participant can select it, they can paste it into the agent, and then the
// agent is working from our wording instead of theirs. The instructions they
// write are one of the things the study measures, so that is not a small loss.
//
// A canvas has no text nodes at all, so there is nothing to select, nothing for
// a screen reader to read out into a clipboard, and nothing a "copy page" gets.
// The words still exist in the page's source, which anyone determined could
// read; this stops the accident, not the determined.

const FONT = '-apple-system, "Segoe UI", system-ui, sans-serif';

/**
 * Render a card into a canvas sized for the container.
 *
 * @param {HTMLElement} el   where it goes
 * @param {object} card      { title, lines }
 * @param {object} opts      { width, dark }
 */
export function drawCard(el, card, { width = 720, dark = false } = {}) {
    const scale = (globalThis.devicePixelRatio || 1);
    const lineHeight = 30;
    const padding = 44;
    const height = padding * 2 + 54 + card.lines.length * lineHeight + 40;

    let canvas = el.querySelector('canvas');
    if (!canvas) {
        canvas = document.createElement('canvas');
        canvas.setAttribute('role', 'img');
        el.append(canvas);
    }
    // The alternative text names the card without repeating it, so a screen
    // reader announces what is on screen and the words still cannot be copied.
    canvas.setAttribute('aria-label', `Task card: ${card.title}. Read aloud by the researcher.`);
    canvas.width = width * scale;
    canvas.height = height * scale;
    canvas.style.width = '100%';
    canvas.style.maxWidth = `${width}px`;
    canvas.style.height = 'auto';

    const ctx = canvas.getContext('2d');
    if (!ctx) {
        // No drawing surface. A card that cannot be seen is worse than one that
        // can be selected, so fall back to text and make it as hard to copy as
        // markup allows. The researcher is on the call and can read it out.
        canvas.remove();
        el.innerHTML = `<div class="card-fallback">
            <strong>${escapeText(card.title)}</strong>
            ${card.lines.map((l) => `<p>${escapeText(l)}</p>`).join('')}
            <em>About 17 minutes. Work as you normally would.</em>
        </div>`;
        return null;
    }
    ctx.scale(scale, scale);

    const ink = dark ? '#ecebe8' : '#1a1a18';
    const soft = dark ? '#a2a09b' : '#6b6862';
    const panel = dark ? '#1d1f23' : '#ffffff';
    const line = dark ? '#2c2f35' : '#e6e4e0';

    ctx.fillStyle = panel;
    roundRect(ctx, 0.5, 0.5, width - 1, height - 1, 12);
    ctx.fill();
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = ink;
    ctx.font = `600 24px ${FONT}`;
    ctx.textBaseline = 'top';
    ctx.fillText(card.title, padding, padding);

    ctx.font = `16px ${FONT}`;
    ctx.fillStyle = soft;
    let y = padding + 54;
    for (const l of card.lines) {
        if (l) ctx.fillText(l, padding, y);
        y += lineHeight;
    }

    ctx.font = `14px ${FONT}`;
    ctx.fillStyle = soft;
    ctx.fillText('About 17 minutes. Work as you normally would.', padding, y + 6);

    return canvas;
}

function escapeText(s) {
    return String(s).replace(/[&<>"]/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
}
