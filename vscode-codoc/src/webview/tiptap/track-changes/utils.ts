/**
 * Sanitize a CSS color value to prevent CSS injection.
 * Strips semicolons, braces, and other characters that could break
 * out of a CSS property value. Returns null if the value is empty
 * or contains only dangerous characters.
 */
export function sanitizeCSSValue(value: unknown): string | null {
  if (!value || typeof value !== 'string') return null;
  // Remove characters that could inject additional CSS properties or rules
  const sanitized = value.replace(/[;{}()<>]/g, '').trim();
  return sanitized || null;
}

let counter = 0;

export function generateChangeId(): string {
  counter++;
  return `ch_${Date.now()}_${counter}_${Math.random().toString(36).slice(2, 8)}`;
}

export function resetChangeIdCounter(): void {
  counter = 0;
}

// Cached Intl.Segmenter instance (created once, reused across calls)
let _segmenter: Intl.Segmenter | null = null;
function getSegmenter(): Intl.Segmenter | null {
  if (_segmenter) return _segmenter;
  if (typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function') {
    _segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    return _segmenter;
  }
  return null;
}

export function lastGraphemeClusterLength(text: string): number {
  if (text.length === 0) return 0;

  const segmenter = getSegmenter();
  if (segmenter) {
    // Iterate to find the last segment without spreading the full array
    let last: string | undefined;
    for (const seg of segmenter.segment(text)) {
      last = seg.segment;
    }
    if (last !== undefined) {
      return last.length;
    }
  }

  // Fallback: handle surrogate pairs and combining marks.
  // Without Intl.Segmenter, walk backward past any combining characters
  // (Unicode General_Category = M) to find the start of the cluster.
  return lastClusterLengthFallback(text);
}

export function firstGraphemeClusterLength(text: string): number {
  if (text.length === 0) return 0;

  const segmenter = getSegmenter();
  if (segmenter) {
    for (const seg of segmenter.segment(text)) {
      return seg.segment.length;
    }
  }

  // Fallback: handle surrogate pairs and combining marks
  return firstClusterLengthFallback(text);
}

// Combining mark ranges (Unicode General_Category = M)
// Covers most complex scripts: Devanagari, Bengali, Tamil, Thai, Lao,
// Khmer, Myanmar, Arabic, Hebrew, Tibetan, etc.
function isCombiningMark(code: number): boolean {
  return (
    (code >= 0x0300 && code <= 0x036f) || // Combining Diacritical Marks
    (code >= 0x0483 && code <= 0x0489) || // Cyrillic combining marks
    (code >= 0x0591 && code <= 0x05bd) || // Hebrew points
    (code >= 0x05bf && code <= 0x05c7) || // Hebrew points (continued)
    (code >= 0x0610 && code <= 0x061a) || // Arabic combining marks
    (code >= 0x064b && code <= 0x065f) || // Arabic tashkeel
    code === 0x0670 ||                    // Arabic superscript alef
    (code >= 0x06d6 && code <= 0x06ed) || // Arabic marks
    code === 0x0711 ||                    // Syriac
    (code >= 0x0730 && code <= 0x074a) || // Syriac marks
    (code >= 0x07a6 && code <= 0x07b0) || // Thaana
    (code >= 0x08d3 && code <= 0x08ff) || // Arabic Extended-A combining marks
    (code >= 0x0900 && code <= 0x0903) || // Devanagari
    (code >= 0x093a && code <= 0x094f) || // Devanagari vowel signs + virama
    (code >= 0x0951 && code <= 0x0957) || // Devanagari stress marks
    (code >= 0x0962 && code <= 0x0963) || // Devanagari vowel signs
    (code >= 0x0981 && code <= 0x0983) || // Bengali
    (code >= 0x09bc && code <= 0x09cd) || // Bengali signs + virama
    (code >= 0x09e2 && code <= 0x09e3) || // Bengali vowel signs
    (code >= 0x0a01 && code <= 0x0a03) || // Gurmukhi
    (code >= 0x0a3c && code <= 0x0a4d) || // Gurmukhi signs
    (code >= 0x0a81 && code <= 0x0a83) || // Gujarati
    (code >= 0x0abc && code <= 0x0acd) || // Gujarati signs
    (code >= 0x0b01 && code <= 0x0b03) || // Oriya
    (code >= 0x0b3c && code <= 0x0b4d) || // Oriya signs
    code === 0x0b82 ||                    // Tamil
    (code >= 0x0bbe && code <= 0x0bcd) || // Tamil vowel signs + virama
    (code >= 0x0c00 && code <= 0x0c04) || // Telugu
    (code >= 0x0c3e && code <= 0x0c4d) || // Telugu signs
    (code >= 0x0c81 && code <= 0x0c83) || // Kannada
    (code >= 0x0cbc && code <= 0x0ccd) || // Kannada signs
    (code >= 0x0d00 && code <= 0x0d03) || // Malayalam
    (code >= 0x0d3b && code <= 0x0d4d) || // Malayalam signs
    code === 0x0e31 ||                    // Thai sara am/combining
    (code >= 0x0e34 && code <= 0x0e3a) || // Thai combining vowels/marks
    (code >= 0x0e47 && code <= 0x0e4e) || // Thai tone marks
    code === 0x0eb1 ||                    // Lao combining
    (code >= 0x0eb4 && code <= 0x0ebc) || // Lao combining vowels
    (code >= 0x0ec8 && code <= 0x0ecd) || // Lao tone marks
    (code >= 0x0f18 && code <= 0x0f19) || // Tibetan
    (code >= 0x0f35 && code <= 0x0f39) || // Tibetan marks
    (code >= 0x0f71 && code <= 0x0f84) || // Tibetan vowel signs
    (code >= 0x0f86 && code <= 0x0f87) || // Tibetan marks
    (code >= 0x0f8d && code <= 0x0fbc) || // Tibetan subjoined consonants
    (code >= 0x102b && code <= 0x103e) || // Myanmar vowel signs + medials
    (code >= 0x1056 && code <= 0x1059) || // Myanmar extensions
    (code >= 0x105e && code <= 0x1060) || // Myanmar extensions
    (code >= 0x1062 && code <= 0x1064) || // Myanmar extensions
    (code >= 0x1067 && code <= 0x106d) || // Myanmar extensions
    (code >= 0x1071 && code <= 0x1074) || // Myanmar extensions
    (code >= 0x1082 && code <= 0x108d) || // Myanmar extensions
    (code >= 0x135d && code <= 0x135f) || // Ethiopic combining
    (code >= 0x17b4 && code <= 0x17d3) || // Khmer vowel signs + coeng
    code === 0x17dd ||                    // Khmer sign atthacan
    (code >= 0x1920 && code <= 0x192b) || // Limbu
    (code >= 0x1930 && code <= 0x193b) || // Limbu
    (code >= 0x1ab0 && code <= 0x1aff) || // Combining Diacritical Marks Extended
    (code >= 0x1dc0 && code <= 0x1dff) || // Combining Diacritical Marks Supplement
    code === 0x200c ||                    // Zero Width Non-Joiner (ZWNJ)
    code === 0x200d ||                    // Zero Width Joiner (ZWJ)
    (code >= 0x20d0 && code <= 0x20ff) || // Combining marks for symbols
    (code >= 0x2de0 && code <= 0x2dff) || // Cyrillic Extended-B combining
    (code >= 0xa66f && code <= 0xa672) || // Cyrillic Extended-B combining marks
    (code >= 0xa8e0 && code <= 0xa8f7) || // Devanagari Extended combining marks
    (code >= 0xfe20 && code <= 0xfe2f)    // Combining Half Marks
  );
}

function lastClusterLengthFallback(text: string): number {
  let len = 0;
  let i = text.length;

  // Walk backward past combining marks
  while (i > 0) {
    const code = text.charCodeAt(i - 1);

    // Handle low surrogate (part of a surrogate pair)
    if (code >= 0xdc00 && code <= 0xdfff && i >= 2) {
      const high = text.charCodeAt(i - 2);
      if (high >= 0xd800 && high <= 0xdbff) {
        const cp = ((high - 0xd800) * 0x400 + (code - 0xdc00)) + 0x10000;
        if (len === 0) {
          // This is the base character (emoji, etc.)
          return 2;
        }
        // Only continue if the surrogate pair is a combining mark
        if (!isCombiningMark(cp)) {
          // It's a base character; include it and stop
          len += 2;
          break;
        }
        len += 2;
        i -= 2;
        continue;
      }
    }

    if (len > 0 && !isCombiningMark(code)) {
      // We've passed all combining marks; stop
      break;
    }

    len += 1;
    i -= 1;

    if (!isCombiningMark(code)) {
      // This was the base character; stop
      break;
    }
  }

  return Math.max(len, 1);
}

function firstClusterLengthFallback(text: string): number {
  let len = 0;
  let i = 0;

  // Read the base character
  const firstCode = text.codePointAt(0);
  if (firstCode === undefined) return 1;

  if (firstCode > 0xffff) {
    len = 2;
    i = 2;
  } else {
    len = 1;
    i = 1;
  }

  // Walk forward past combining marks, using codePointAt to correctly
  // handle surrogate pairs for characters above U+FFFF.
  while (i < text.length) {
    const cp = text.codePointAt(i);
    if (cp === undefined) break;
    const charLen = cp > 0xffff ? 2 : 1;

    if (!isCombiningMark(cp)) break;
    len += charLen;
    i += charLen;
  }

  return len;
}
