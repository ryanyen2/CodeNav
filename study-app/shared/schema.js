// Where things live in Firestore, and what may never be written there.
//
// The rules file is the enforcement. This module is the same shape expressed in
// JavaScript, so the pages and the mirror agree with the rules instead of each
// guessing. When you change one, change both, and the rule tests will tell you if
// they have drifted apart.

/** Collection and document paths. `code` is the participant code. */
export const paths = {
    participant: (code) => `participants/${code}`,
    devices: (code) => `participants/${code}/devices`,
    device: (code, slot) => `participants/${code}/devices/${slot}`,
    answers: (code) => `participants/${code}/answers`,
    answer: (code, form) => `participants/${code}/answers/${form}`,
    session: (code, condition) => `participants/${code}/sessions/${condition}`,
    batches: (code, condition) => `participants/${code}/sessions/${condition}/batches`,
    batch: (code, condition, id) => `participants/${code}/sessions/${condition}/batches/${id}`,
    // Written by the experimenter only, kept apart from the participant's own
    // session document so neither can overwrite the other and the rules stay
    // simple enough to read.
    assessment: (code, condition) => `participants/${code}/assessments/${condition}`,
};

/**
 * Exactly two things write under a participant: the page in their browser and
 * the mirror inside their editor. Naming the slots instead of counting devices
 * makes the limit structural, so the rules do not need to count anything.
 */
export const DEVICE_SLOTS = Object.freeze(['browser', 'mirror']);

/** The two conditions, used as session document ids. */
export const CONDITIONS = Object.freeze(['codoc', 'baseline']);

/**
 * Field names that must never reach Firestore. Consent is collected in a Google
 * Form and the identifying fields stay there; this database only ever holds a
 * code. The rules reject any document carrying one of these keys, so a future
 * page cannot start sending names by accident.
 */
export const FORBIDDEN_FIELDS = Object.freeze([
    'name', 'fullName', 'firstName', 'lastName',
    'email', 'emailAddress',
    'phone', 'address', 'dob', 'dateOfBirth', 'ip',
]);

/** True when an object carries any field the rules will reject. */
export function hasForbiddenField(data) {
    return Object.keys(data || {}).some((k) => FORBIDDEN_FIELDS.includes(k));
}

/**
 * A participant code. Long and random, because it is the only thing standing
 * between a stranger and a write. Ambiguous characters are left out so a code
 * can be read aloud over a call without confusion.
 */
const ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789';

export function newParticipantCode(random = defaultRandom) {
    const body = Array.from({ length: 12 }, () => ALPHABET[random(ALPHABET.length)]).join('');
    return `p-${body}`;
}

function defaultRandom(n) {
    const c = globalThis.crypto;
    if (c && c.getRandomValues) {
        const a = new Uint32Array(1);
        // Reject the tail of the range so every letter is equally likely.
        const limit = Math.floor(0xffffffff / n) * n;
        let v;
        do { c.getRandomValues(a); v = a[0]; } while (v >= limit);
        return v % n;
    }
    return Math.floor(Math.random() * n);
}

export const CODE_PATTERN = /^p-[abcdefghjkmnpqrstuvwxyz23456789]{12}$/;
