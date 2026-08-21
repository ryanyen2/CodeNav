/**
 * Where a settings section is, in the file it lives in.
 *
 * A description can now cite a configured decision — `[month = made](codoc:tally/rules.toml#periods)`
 * — because the sections of a settings file this repo's own code reads are indexed
 * chunks (`codoc/settings_files.py`). Clicking one has to land on the section, and
 * nothing that finds CODE can do it: the document-symbol provider needs a language
 * server nobody installs for TOML, and the fallback regex looks for `def` / `class` /
 * `name =`, which no section header matches. So the click opened the file at line one
 * and revealed nothing — which reads as a broken citation rather than a missing parser,
 * and a citation a reader has learned not to trust is worse than no citation.
 *
 * The leaf rule is wrong here too, and that is the deeper reason this is a module
 * rather than one more branch in `openRef`. `symbolLeaf` takes the last `.`-segment
 * because `file.py::Class.method` DECLARES `method`; a settings path nests in a
 * document, so `pyproject.toml::tool.pytest.ini_options` is declared by the whole
 * dotted name and its last segment names nothing at all.
 *
 * This mirrors `_section_starts` / `_uniquify` / `_json_key_lines` in
 * `codoc/settings_files.py` — the header grammars, the `name[1]` disambiguation of a
 * repeated array-of-tables header, and JSON's depth scan. It reads headers only and
 * never values, so the two can disagree about nothing except where a section begins.
 */

/** Extension → format name, mirroring `FORMATS` in `codoc/settings_files.py`. */
const FORMATS: Record<string, string> = {
    '.toml': 'toml',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.json': 'json',
    '.ini': 'ini',
    '.cfg': 'ini',
};

/** The settings format of `file`, or null when it is not a settings file. */
export function settingsFormat(file: string): string | null {
    const dot = file.lastIndexOf('.');
    if (dot < 0) return null;
    return FORMATS[file.slice(dot).toLowerCase()] ?? null;
}

const TOML_SECTION = /^\s*\[\[?\s*([^[\]]+?)\s*\]\]?\s*(?:#.*)?$/;
const INI_SECTION = /^\s*\[\s*([^[\]]+?)\s*\]\s*$/;
const YAML_KEY = /^([A-Za-z_][\w.-]*)\s*:/;

/** A TOML table name as a symbol-path segment: quotes off, dots kept. */
function dotted(name: string): string {
    return name.split('.').map(part => part.trim().replace(/^["']|["']$/g, '')).join('.');
}

/** Top-level keys of a JSON object, by the line each opens on.
 *
 *  A depth count rather than a per-line regex, because `"month":` nested inside
 *  `"periods"` is not a section and a line-wise match cannot tell the two apart — it
 *  would send a reader who clicked `periods` to whichever key came first. Strings are
 *  skipped whole so a brace inside a value cannot move the depth. */
function jsonKeyLines(text: string): { line: number; name: string }[] {
    const out: { line: number; name: string }[] = [];
    let depth = 0;
    let line = 0;
    let i = 0;
    while (i < text.length) {
        const ch = text[i];
        if (ch === '\n') {
            line += 1;
        } else if (ch === '"') {
            const start = i;
            i += 1;
            while (i < text.length && text[i] !== '"') i += text[i] === '\\' ? 2 : 1;
            if (depth === 1 && text.slice(i + 1).replace(/^[ \t]*/, '').startsWith(':')) {
                out.push({ line, name: text.slice(start + 1, i) });
            }
        } else if (ch === '{' || ch === '[') {
            depth += 1;
        } else if (ch === '}' || ch === ']') {
            depth -= 1;
        }
        i += 1;
    }
    return out;
}

/** (line, section name) for every header in `text`, in file order, with names made
 *  unique the way the chunker makes them: the first `[[servers]]` keeps the plain name
 *  and the second becomes `servers[1]`, since two chunks may not share a symbol path. */
function sectionStarts(text: string, format: string): { line: number; name: string }[] {
    let raw: { line: number; name: string }[];
    if (format === 'json') {
        raw = jsonKeyLines(text);
    } else {
        const pattern = format === 'toml' ? TOML_SECTION : format === 'ini' ? INI_SECTION
            : format === 'yaml' ? YAML_KEY : null;
        if (!pattern) return [];
        raw = [];
        text.split('\n').forEach((line, i) => {
            const m = pattern.exec(line);
            if (m) raw.push({ line: i, name: format === 'toml' ? dotted(m[1]) : m[1] });
        });
    }
    const seen = new Map<string, number>();
    return raw.map(({ line, name }) => {
        const count = seen.get(name) ?? 0;
        seen.set(name, count + 1);
        return { line, name: count ? `${name}[${count}]` : name };
    });
}

/** The 0-based line of the HEADER of the section a `codoc:` ref names, or -1.
 *
 *  The header and not the chunk's first line, which is the comment run above it
 *  (`extract_chunks` starts a section there so a quote carries its reasoning). Both
 *  are on screen once the range is revealed, and the header is the line that says
 *  which section this is — so the flash lands on `[periods]` rather than on a
 *  sentence about it.
 *
 *  `symbol` may arrive qualified (`tally/rules.toml::periods`) or bare (`periods`),
 *  because both are authored. `__module__` — the keys above the first section — is the
 *  top of the file, which is where it is.
 *
 *  Exact match only, and a miss is reported rather than approximated. A settings ref
 *  naming no section is a stale citation; landing the reader on the nearest section
 *  would show them a decision that is not the one the sentence claimed, which is a
 *  worse failure than not moving.
 */
export function settingsSectionLine(text: string, symbol: string, format: string): number {
    const name = symbol.includes('::') ? symbol.split('::').pop()! : symbol;
    if (!name || name === '__module__') return 0;
    for (const section of sectionStarts(text, format)) {
        if (section.name === name) return section.line;
    }
    return -1;
}
