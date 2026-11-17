/**
 * markdown.ts: the reading half of the Markdown form, for paste.
 *
 * A port of `codoc/calculus/block_md.parse` and `inline_md.parse`; the format
 * is docs/model.md, "The Markdown form". Both must give the same blocks and
 * spans for the same text: `src/test/fixtures/markdown-cases.json` holds the
 * Python codec's output and the tests compare against it.
 *
 * Offsets are Unicode scalars, as everywhere the store counts, so the parsers
 * work on arrays of code points rather than on UTF-16 strings.
 */
import type { Layout, Span } from './projection';

export interface BlockSpec {
    kind: string;
    level: number;
    /** Inline Markdown for a heading or prose kind; the raw source for a source kind. */
    text: string;
    /** The non-Text fields: `lang`, `derived`, or a `codoc:` fence's JSON. */
    content: Record<string, unknown>;
    /** Set only where the Markdown carried an id. */
    id: string | null;
}

const MAX_LEVEL = 6;

/** Where a plugin block's `codoc:` fence carries its layout. */
export const LAYOUT_KEY = '$layout';

/** The layout a `codoc:` fence carries, as `doc.Layout.from_json` reads it, or
 *  null when it has none or it is malformed. */
export function fenceLayout(content: Record<string, unknown>): Layout | null {
    const d = content[LAYOUT_KEY];
    const text = d && typeof d === 'object' ? (d as Record<string, unknown>).text ?? [] : null;
    if (!Array.isArray(text) || !text.every(f => typeof f === 'string')) return null;
    const o = d as Record<string, unknown>;
    return { text: [...new Set(text as string[])], flow: o.flow === undefined ? true : !!o.flow, export: o.export === undefined ? true : !!o.export };
}
const SOURCE = new Set(['table', 'diagram', 'code']);
// `.` with the `s` flag matches what Python's `.` does on a line with no newline.
const HEADING = /^(#{1,6}) (.*?)(?: \{#(b-[0-9a-f]+)\})?$/s;
const ITEM = /^([ \t]*)(?:[-+*]|(\d{1,9})\.) +(.*)$/s;
const FENCE = /^(`{3,})\s*([^\s`{]*)([^`{]*?)(?:\{#(b-[0-9a-f]+)\})?\s*$/;

const spec = (kind: string, level = 0, text = '', content: Record<string, unknown> = {}, id: string | null = null): BlockSpec =>
    ({ kind, level, text, content, id });

/** The blocks `md` describes, in order. With `headings` false a `# line` is a
 *  paragraph without its hashes. With `joinLines` false each paragraph line is
 *  a paragraph of its own, as a paste wants; the store's reader joins them. */
export function parseBlocks(md: string, headings = true, joinLines = true): BlockSpec[] {
    const out: BlockSpec[] = [];
    const lines = md.replace(/\r\n/g, '\n').split('\n');
    const para: string[] = [];
    const indents: number[] = [];
    let i = 0;

    const flush = () => {
        if (!para.length) return;
        const texts = !joinLines ? para.map(l => l.trim())
            : [para.length === 1 ? para[0].trim() : para.map(l => l.trim()).join(' ')];
        para.length = 0;
        for (const text of texts) if (text) out.push(spec('paragraph', 0, text));
    };

    while (i < lines.length) {
        const line = lines[i];
        const stripped = line.trim();
        if (!stripped) { flush(); i++; continue; }
        const fence = FENCE.exec(stripped);
        if (fence) {
            flush();
            indents.length = 0;
            const [body, next] = fenceBody(lines, i + 1, fence[1].length);
            i = next;
            out.push(fenced(fence[2], fence[3].split(/\s+/).filter(Boolean), fence[4] ?? null, body));
            continue;
        }
        if (stripped.startsWith('|')) {
            flush();
            indents.length = 0;
            const rows: string[] = [];
            while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(lines[i++].trim());
            out.push(spec('table', 0, rows.join('\n')));
            continue;
        }
        const item = ITEM.exec(line);
        if (item) {
            flush();
            const width = columns(item[1]);
            while (indents.length && width < indents[indents.length - 1]) indents.pop();
            if (!indents.length || width > indents[indents.length - 1]) indents.push(width);
            let text = item[3].trim();
            i++;
            // A wrapped item: indented lines that are not items themselves.
            while (i < lines.length && (lines[i][0] === ' ' || lines[i][0] === '\t') && lines[i].trim()
                   && !ITEM.test(lines[i]) && !lines[i].trim().startsWith('|') && !lines[i].trim().startsWith('```')) {
                text += ' ' + lines[i].trim();
                i++;
            }
            out.push(spec(item[2] !== undefined ? 'numbered' : 'bullet', Math.min(indents.length, MAX_LEVEL), text));
            continue;
        }
        const h = HEADING.exec(stripped);
        if (h) {
            flush();
            indents.length = 0;
            if (headings) out.push(spec('heading', h[1].length, h[2].trim(), {}, h[3] ?? null));
            else if (h[2].trim()) out.push(spec('paragraph', 0, h[2].trim()));
            i++;
            continue;
        }
        indents.length = 0;
        para.push(line);
        i++;
    }
    flush();
    return out;
}

/** The width of an indent, a tab advancing to the next multiple of four. */
function columns(indent: string): number {
    let col = 0;
    for (const c of indent) col = c === '\t' ? col + 4 - (col % 4) : col + 1;
    return col;
}

/** Lines up to the closing fence (at least `ticks` backticks and nothing else),
 *  and the index after it. An unclosed fence runs to the end. */
function fenceBody(lines: string[], i: number, ticks: number): [string, number] {
    const body: string[] = [];
    for (; i < lines.length; i++) {
        const s = lines[i].trim();
        if (s.length >= ticks && /^`+$/.test(s)) return [body.join('\n'), i + 1];
        body.push(lines[i]);
    }
    return [body.join('\n'), i];
}

function fenced(info: string, words: string[], bid: string | null, body: string): BlockSpec {
    if (info === 'mermaid') return spec('diagram', 0, body, words.includes('derived') ? { derived: true } : {});
    if (info.startsWith('codoc:')) {
        let content: unknown = null;
        try { content = JSON.parse(body || '{}'); } catch { content = null; }
        if (content && typeof content === 'object' && !Array.isArray(content)) {
            const kind = info.slice('codoc:'.length);
            const c = content as Record<string, unknown>;
            let source: unknown = '';
            if (SOURCE.has(kind)) { source = c.source ?? ''; delete c.source; }
            return spec(kind, 0, typeof source === 'string' ? source : '', c, bid);
        }
    }
    return spec('code', 0, body, info ? { lang: info } : {});
}

// ── inline ──────────────────────────────────────────────────────────────────

const ESCAPABLE = new Set('*[]\\#`-+.|');
const CODE_ESCAPABLE = new Set('`\\');
const TARGET = /^\((codoc:[^)\s]+|https?:\/\/[^)\s]+)\)/;

/** Plain text and its spans. A span that holds others comes before them. */
export function parseInline(md: string): { text: string; spans: Span[] } {
    const cs = Array.from(md);
    const out: string[] = [];
    const spans: Span[] = [];
    scan(cs, 0, null, out, spans, true, true);
    return { text: out.join(''), spans };
}

const at = (cs: string[], i: number, s: string): boolean => cs[i] === s[0] && (s.length === 1 || cs[i + 1] === s[1]);

/** Scan from `i` until `stop` (`]` or `**`) or the end, appending characters
 *  to `out` and spans to `spans`. Returns the index of the stop marker, or -1
 *  when a stop was wanted and the text ended first. */
function scan(cs: string[], i: number, stop: ']' | '**' | null, out: string[], spans: Span[], links: boolean, bold: boolean): number {
    const n = cs.length;
    while (i < n) {
        const c = cs[i];
        let end: number;
        let j: number;
        if (c === '\\' && i + 1 < n && ESCAPABLE.has(cs[i + 1])) {
            out.push(cs[i + 1]);
            i += 2;
        } else if (c === '`' && (end = codeEnd(cs, i + 1)) > 0) {
            const start = out.length;
            out.push(...Array.from(cs.slice(i + 1, end).join('').replace(/\\([`\\])/g, '$1')));
            spans.push({ kind: 'code', attrs: {}, start, end: out.length });
            i = end + 1;
        } else if (stop === ']' && c === ']') {
            return i;
        } else if (stop === '**' && at(cs, i, '**')) {
            return i;
        } else if (c === '[' && links && (j = link(cs, i, out, spans)) > 0) {
            i = j;
        } else if (bold && at(cs, i, '**') && (j = boldAt(cs, i, out, spans)) > 0) {
            i = j;
        } else {
            out.push(c);
            i++;
        }
    }
    return stop ? -1 : n;
}

/** Index of the backtick closing a code span whose content starts at `i`, or
 *  -1 (no closing backtick on this line, or an empty span). */
function codeEnd(cs: string[], i: number): number {
    let j = i;
    while (j < cs.length && cs[j] !== '\n') {
        if (cs[j] === '\\' && j + 1 < cs.length && CODE_ESCAPABLE.has(cs[j + 1])) j += 2;
        else if (cs[j] === '`') return j > i ? j : -1;
        else j++;
    }
    return -1;
}

/** `[label](target)` at `i`: append it and return the index after it, or -1. */
function link(cs: string[], i: number, out: string[], spans: Span[]): number {
    const label: string[] = [];
    const inner: Span[] = [];
    const close = scan(cs, i + 1, ']', label, inner, false, true);
    if (close < 0) return -1;
    const m = TARGET.exec(cs.slice(close + 1).join(''));
    if (!m) return -1;
    const start = out.length;
    const target = m[1];
    if (target.startsWith('codoc:')) {
        const rest = target.slice('codoc:'.length);
        const hash = rest.indexOf('#');
        const [file, symbol] = hash < 0 ? [rest, ''] : [rest.slice(0, hash), rest.slice(hash + 1)];
        spans.push({ kind: 'cite', attrs: { file, symbol }, start, end: start + label.length });
    } else {
        spans.push({ kind: 'link', attrs: { href: target }, start, end: start + label.length });
    }
    for (const s of inner) spans.push({ ...s, start: s.start + start, end: s.end + start });
    out.push(...label);
    return close + 1 + Array.from(m[0]).length;
}

/** `**text**` at `i`: append it and return the index after it, or -1 when it
 *  is not closed on this line or holds nothing. */
function boldAt(cs: string[], i: number, out: string[], spans: Span[]): number {
    const text: string[] = [];
    const inner: Span[] = [];
    const close = scan(cs, i + 2, '**', text, inner, false, false);
    if (close < 0 || !text.length || text.includes('\n')) return -1;
    const start = out.length;
    spans.push({ kind: 'bold', attrs: {}, start, end: start + text.length });
    for (const s of inner) spans.push({ ...s, start: s.start + start, end: s.end + start });
    out.push(...text);
    return close + 2;
}

// ── writing ─────────────────────────────────────────────────────────────────

const DEPTH: Record<string, number> = { link: 0, cite: 0, bold: 1, code: 2 };
const OPEN: Record<string, string> = { link: '[', cite: '[', bold: '**', code: '`' };

function closeOf(s: Span): string {
    if (s.kind === 'cite') return `](codoc:${s.attrs.file ?? ''}${s.attrs.symbol ? `#${s.attrs.symbol}` : ''})`;
    if (s.kind === 'link') return `](${s.attrs.href ?? ''})`;
    return OPEN[s.kind];
}

/** The spans of `kind`, each cut at every edge that falls inside it. */
function cutAt(spans: Span[], kind: string, edges: number[]): Span[] {
    const out: Span[] = [];
    for (const s of spans) {
        if (s.kind !== kind || s.end <= s.start) continue;
        const cuts = [...new Set([s.start, s.end, ...edges.filter(p => s.start < p && p < s.end)])].sort((a, b) => a - b);
        for (let i = 0; i + 1 < cuts.length; i++) out.push({ kind, attrs: {}, start: cuts[i], end: cuts[i + 1] });
    }
    return out;
}

/** Inline Markdown for `text` with `spans`: a port of `inline_md.render`.
 *  Characters that would read as markup are escaped. */
export function renderInline(text: string, spans: Span[]): string {
    const cs = Array.from(text);
    const links = spans.filter(s => (s.kind === 'link' || s.kind === 'cite') && s.end > s.start).sort((a, b) => a.start - b.start);
    const bolds = cutAt(spans, 'bold', links.flatMap(l => [l.start, l.end]));
    const codes = cutAt(spans, 'code', [...links, ...bolds].flatMap(s => [s.start, s.end]));
    const opens = new Map<number, Span[]>(), closes = new Map<number, Span[]>();
    for (const s of [...links, ...bolds, ...codes]) {
        opens.set(s.start, [...(opens.get(s.start) ?? []), s]);
        closes.set(s.end, [...(closes.get(s.end) ?? []), s]);
    }
    const covered = (list: Span[]) => {
        const f = new Array<boolean>(cs.length + 1).fill(false);
        for (const s of list) for (let i = s.start; i < s.end; i++) f[i] = true;
        return f;
    };
    const inLabel = covered(links), inCode = covered(codes);
    const boldEdge = new Set(bolds.flatMap(s => [s.start, s.end]));
    let out = '';
    for (let i = 0; i <= cs.length; i++) {
        for (const s of [...(closes.get(i) ?? [])].sort((a, b) => DEPTH[b.kind] - DEPTH[a.kind])) out += closeOf(s);   // innermost first
        for (const s of [...(opens.get(i) ?? [])].sort((a, b) => DEPTH[a.kind] - DEPTH[b.kind])) out += OPEN[s.kind];   // outermost first
        if (i < cs.length) out += escaped(cs, i, inLabel[i], inCode[i], boldEdge.has(i) || boldEdge.has(i + 1));
    }
    return out;
}

function escaped(cs: string[], i: number, inLabel: boolean, inCode: boolean, atBoldEdge: boolean): string {
    const c = cs[i];
    if (inCode) return CODE_ESCAPABLE.has(c) ? `\\${c}` : c;
    if (c === '\\') return '\\\\';
    if (c === '*' && (atBoldEdge || cs[i - 1] === '*' || cs[i + 1] === '*')) return '\\*';
    if (c === ']' && inLabel) return '\\]';
    if (c === '[' || c === '`') return `\\${c}`;      // a plain bracket never opens a link, a plain backtick never opens code
    return opensBlock(cs, i) ? `\\${c}` : c;
}

/** Would `cs[i]` make the line read as a heading, a list item or a table row? */
function opensBlock(cs: string[], i: number): boolean {
    const c = cs[i];
    if (i === 0) return c === '#' || c === '|' || ('-+*'.includes(c) && cs[1] === ' ');
    return c === '.' && i <= 9 && /^\d+$/.test(cs.slice(0, i).join('')) && cs[i + 1] === ' ';
}

/** JSON with every object's keys sorted, as `json.dumps(sort_keys=True)` orders them. */
const sortedJson = (v: unknown): string => JSON.stringify(v, (_k, x: unknown) =>
    (x && typeof x === 'object' && !Array.isArray(x)
        ? Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : x));

/** A block of a non-builtin kind as its `codoc:` fence: `$layout`, every other
 *  field as JSON, and each non-empty Text field as inline Markdown. `fields`
 *  are projected fields, where a Text field is `{text, spans, ...}`. */
export function fenceOf(kind: string, fields: Record<string, unknown>, layout: Layout): string {
    const obj: Record<string, unknown> = { [LAYOUT_KEY]: layout };
    for (const [k, v] of Object.entries(fields)) {
        if (!layout.text.includes(k)) obj[k] = v;
        else if ((v as { text?: string })?.text) obj[k] = renderInline((v as { text: string }).text, (v as { spans?: Span[] }).spans ?? []);
    }
    return `\`\`\`codoc:${kind}\n${sortedJson(obj)}\n\`\`\``;
}
