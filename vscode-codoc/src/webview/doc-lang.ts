/**
 * doc-lang.ts — the authoring language, webview side (pure, no vscode import).
 *
 * The tree may be deliberately bilingual: an author describing intent in Chinese
 * can still have written one node in English and meant to. So the view renders from
 * two things — the tree's language on the document root, and a per-node `lang` for
 * the rows that differ (the sidecar omits it when a node matches the tree, so a
 * monolingual tree tags nothing).
 *
 * `lang` and not a CSS class, because the attribute is what the *browser* reads: it
 * picks per-element font fallback, line-breaking rules (a CJK line may break between
 * any two characters, a Latin one may not), and quotation conventions from it. A
 * class would let us style, and leave the layout wrong.
 */

/** The languages the switcher offers — mirrors `codoc/doclang._PROFILES` (parity
 *  tested). Any other BCP-47 tag still works via `codoc lang <tag>`; this is the
 *  menu, not the limit. */
export const DOC_LANGUAGE_CHOICES: { code: string; name: string }[] = [
    { code: 'en', name: 'English' },
    { code: 'zh-Hans', name: 'Simplified Chinese / 简体中文' },
    { code: 'zh-Hant', name: 'Traditional Chinese / 繁體中文' },
    { code: 'ja', name: 'Japanese / 日本語' },
    { code: 'ko', name: 'Korean / 한국어' },
];

/** The endonym alone ("简体中文", "日本語") — what a reader of that language
 *  recognizes fastest, and short enough for a toolbar button. Falls back to the raw
 *  tag for a language set from the CLI with no entry here. */
export function shortLanguageLabel(code: string): string {
    const name = DOC_LANGUAGE_CHOICES.find(c => c.code === code)?.name;
    if (!name) return code || 'en';
    const parts = name.split('/').map(p => p.trim()).filter(Boolean);
    return parts[parts.length - 1] || code;
}

/** The full name for a tooltip / menu row, falling back to the tag. */
export function languageName(code: string): string {
    return DOC_LANGUAGE_CHOICES.find(c => c.code === code)?.name ?? (code || 'en');
}

/**
 * The `lang` value an element should carry, or `null` for "inherit".
 *
 * Returning null for the common case is the point: `lang` inherits, so stamping
 * every row with the tree's own language would be pure noise in the DOM and would
 * make the exceptions — the whole reason the attribute is here — invisible to
 * anyone inspecting it.
 */
export function langAttrFor(nodeLang: string | undefined, treeLang: string): string | null {
    const tag = (nodeLang ?? '').trim();
    if (!tag || tag === treeLang) return null;
    return tag;
}
