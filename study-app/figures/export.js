// Getting a figure out of the browser and into the paper.
//
// The figures set every visual property as an SVG attribute, so serializing the
// element is the whole job: no walk over computed styles, no rasterized layer,
// and text stays text. That is what makes the label selectable in the finished
// PDF and re-typesettable when a reviewer asks for a bigger font.
//
// CSV comes out beside every figure, always. A figure a reader cannot check the
// numbers behind is a picture, and the numbers are what a reviewer asks for.

export function serialize(node) {
    const clone = node.cloneNode(true);
    if (!clone.getAttribute('viewBox')) {
        const w = node.getAttribute('width');
        const h = node.getAttribute('height');
        if (w && h) clone.setAttribute('viewBox', `0 0 ${w} ${h}`);
    }
    // Anything that only exists for the interactive view goes.
    clone.querySelectorAll('[data-screen-only]').forEach((n) => n.remove());
    return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}`;
}

function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function downloadSvg(node, filename) {
    if (!node) return;
    download(new Blob([serialize(node)], { type: 'image/svg+xml;charset=utf-8' }), filename);
}

/** A raster copy at print resolution, for slides and for pasting into email. */
export async function downloadPng(node, filename, scale = 3) {
    if (!node) return;
    const w = Number(node.getAttribute('width'));
    const h = Number(node.getAttribute('height'));
    const url = URL.createObjectURL(
        new Blob([serialize(node)], { type: 'image/svg+xml;charset=utf-8' }));
    const img = new Image();
    await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error('could not rasterize the figure'));
        img.src = url;
    });
    const canvas = document.createElement('canvas');
    canvas.width = w * scale;
    canvas.height = h * scale;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(url);
    await new Promise((resolve) => canvas.toBlob((b) => {
        if (b) download(b, filename);
        resolve();
    }, 'image/png'));
}

export function toCsv(rows) {
    if (!rows.length) return '';
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    const cell = (v) => {
        if (v == null) return '';
        const s = String(v);
        return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n');
}

export function downloadCsv(rows, filename) {
    const body = toCsv(rows);
    if (body) download(new Blob([body], { type: 'text/csv;charset=utf-8' }), filename);
}
