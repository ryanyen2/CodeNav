// The participant page in the participant's own language.
//
// One rule shapes all of this: a session runs entirely in ONE language, or not at
// all. A participant does one project each way, so if only the codoc arm's
// description were translated, language would vary with condition and every
// result would be as attributable to reading in a second language as to the tool.
// The descriptions, the questions, the briefings, the task cards and this page
// all move together, or none of them do.
//
// The instrument itself is NOT duplicated. `instrument.js` stays the one place
// that says what is asked, in what order, on which scale, and which items are
// reverse keyed — a second copy of that is how a reverse-keyed item ends up
// averaged the wrong way up in one language and not the other. What lives here is
// only the WORDS, keyed by the same ids, so a translation cannot silently change
// the instrument's shape.
//
// A missing key falls back to the English text and is recorded, so a gap shows up
// as English prose in front of a participant rather than as `prestudy.years.label`
// — and as a failing test before that.

import { ZH_HANS } from './zh-Hans.js';
import { LANGUAGES as CODES, DEFAULT_LANGUAGE } from '../../shared/schema.js';

export { DEFAULT_LANGUAGE };

/**
 * What each language is called and where its words live.
 *
 * The CODES come from the shared schema, which is also what the dashboard offers
 * and what the rules will see, so a language cannot exist on one side and not the
 * other. A code with no table here runs in English, and the test below says so.
 */
const TABLES = { en: null, 'zh-Hans': ZH_HANS };
const NAMES = {
    en: { name: 'English', endonym: 'English' },
    'zh-Hans': { name: 'Chinese (Simplified)', endonym: '简体中文' },
};

export const LANGUAGES = Object.freeze(Object.fromEntries(
    CODES.map((code) => [code, { ...NAMES[code], strings: TABLES[code] ?? null }])));

let current = DEFAULT_LANGUAGE;
let table = null;
const missed = new Set();

/** Whether a code is one we can actually run somebody in. */
export const isLanguage = (code) => Object.hasOwn(LANGUAGES, code || '');

export function setLanguage(code) {
    current = isLanguage(code) ? code : DEFAULT_LANGUAGE;
    table = LANGUAGES[current].strings;
    missed.clear();
    return current;
}

export const language = () => current;

/**
 * The words for `key`, or `english` if this language has none.
 *
 * English is always the fallback rather than the key itself, because the failure
 * this is most likely to have is one string added and not yet translated, and a
 * participant reading one English sentence in a Chinese page is a blemish, while
 * one reading `reflect.next.label` is a broken study.
 */
export function t(key, english) {
    if (!table) return english;
    const found = table[key];
    if (found === undefined) { missed.add(key); return english; }
    return found;
}

/** Every key asked for in this language that had no entry. For the tests. */
export const missingKeys = () => [...missed].sort();

/**
 * The words for one instrument item, without copying its shape.
 *
 * `set` names the block ('prestudy', 'after', 'signoff', …) and the item keeps its
 * own id, so `{ id: 'years', label: 'Years you have been programming' }` is looked
 * up as `prestudy.years.label`. Fields the item does not have are not invented.
 */
export function localize(set, item, fields = ['label', 'text', 'title', 'description', 'placeholder', 'low', 'high']) {
    const out = { ...item };
    for (const field of fields) {
        if (typeof item[field] === 'string') {
            out[field] = t(`${set}.${item.id}.${field}`, item[field]);
        }
    }
    if (Array.isArray(item.options)) {
        out.options = item.options.map((option, i) => (typeof option === 'string'
            ? t(`${set}.${item.id}.option.${i}`, option)
            : { ...option, text: t(`${set}.${item.id}.option.${option.letter}`, option.text) }));
    }
    return out;
}

/** The same, for a whole block. */
export const localizeAll = (set, items, fields) =>
    items.map((item) => localize(set, item, fields));
