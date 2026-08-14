// What the security rules must and must not allow.
//
// These run against the Firestore emulator. They are written before the rules
// themselves on purpose: the rules are the only thing between this project and an
// open database of study data, and they are cheap to get subtly wrong.
//
//   npm test          (starts the emulator, runs this, shuts it down)
//
// The threat model is small and specific. Participant codes are long and random
// and are the write credential. Anyone holding a code can append to that one
// participant's stream, which is bounded and visible. Nobody can read anyone
// else's data, nobody can read anything without being on the allowlist, and no
// identifying field can be written at all.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import test, { before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
    initializeTestEnvironment,
    assertFails,
    assertSucceeds,
} from '@firebase/rules-unit-testing';
import { doc, getDoc, setDoc, collection, getDocs, addDoc } from 'firebase/firestore';

const here = dirname(fileURLToPath(import.meta.url));
const PROJECT = 'codoc-study-rules-test';
const ALLOWED = 'ryanyen2@mit.edu';
const OUTSIDER = 'someone@example.com';
const CODE = 'p-abcdefghjkmn';
const OTHER = 'p-nmkjhgfedcba';

let env;

before(async () => {
    env = await initializeTestEnvironment({
        projectId: PROJECT,
        firestore: {
            rules: readFileSync(join(here, '..', 'firestore.rules'), 'utf8'),
            host: '127.0.0.1',
            port: 8080,
        },
    });
});

after(async () => { if (env) await env.cleanup(); });

beforeEach(async () => {
    await env.clearFirestore();
    // Two participants exist, created by an experimenter, as they would be in
    // practice before anyone joins a call.
    await env.withSecurityRulesDisabled(async (ctx) => {
        const db = ctx.firestore();
        for (const code of [CODE, OTHER]) {
            await setDoc(doc(db, `participants/${code}`), {
                createdAt: Date.now(), order: 'codoc-first', released: false,
            });
        }
    });
});

const experimenter = () => env.authenticatedContext('exp-uid', {
    email: ALLOWED, email_verified: true,
}).firestore();
const outsider = () => env.authenticatedContext('out-uid', {
    email: OUTSIDER, email_verified: true,
}).firestore();
const anon = (uid) => env.authenticatedContext(uid, { provider_id: 'anonymous' }).firestore();
const nobody = () => env.unauthenticatedContext().firestore();

/** Register an anonymous account against a code, the way both writers do. */
async function claim(db, code, slot, uid) {
    return setDoc(doc(db, `participants/${code}/devices/${slot}`), {
        uid, kind: slot, registeredAt: Date.now(),
    });
}

// ── registering a device ─────────────────────────────────────────────────────

test('an anonymous account can register against a code that exists', async () => {
    await assertSucceeds(claim(anon('u1'), CODE, 'browser', 'u1'));
});

test('the browser and the mirror can both register against one code', async () => {
    await assertSucceeds(claim(anon('u1'), CODE, 'browser', 'u1'));
    await assertSucceeds(claim(anon('u2'), CODE, 'mirror', 'u2'));
});

test('a slot name outside the allowed set is refused', async () => {
    await assertFails(claim(anon('u1'), CODE, 'laptop', 'u1'));
});

test('a slot that is already taken cannot be claimed by someone else', async () => {
    await assertSucceeds(claim(anon('u1'), CODE, 'browser', 'u1'));
    await assertFails(claim(anon('u2'), CODE, 'browser', 'u2'));
});

test('a code that does not exist cannot be registered against', async () => {
    await assertFails(claim(anon('u1'), 'p-zzzzzzzzzzzz', 'browser', 'u1'));
});

test('registering with a uid that is not your own is refused', async () => {
    await assertFails(claim(anon('u1'), CODE, 'browser', 'someone-else'));
});

test('a released code cannot be registered against until the slot is cleared', async () => {
    await env.withSecurityRulesDisabled(async (ctx) => {
        await setDoc(doc(ctx.firestore(), `participants/${CODE}`), {
            createdAt: Date.now(), order: 'codoc-first', released: true,
        });
    });
    await assertFails(claim(anon('u1'), CODE, 'browser', 'u1'));
});

// ── writing a session ────────────────────────────────────────────────────────

test('a registered device can append an action batch under its own code', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'mirror', 'u1');
    await assertSucceeds(addDoc(collection(db, `participants/${CODE}/sessions/codoc/batches`), {
        seq: 1, actions: [{ t: 1, a: 'READ_CODE' }],
    }));
});

test('a device cannot write under a different code', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'mirror', 'u1');
    await assertFails(addDoc(collection(db, `participants/${OTHER}/sessions/codoc/batches`), {
        seq: 1, actions: [],
    }));
});

test('an account that never registered cannot write, even with a real code', async () => {
    await assertFails(addDoc(collection(anon('u9'), `participants/${CODE}/sessions/codoc/batches`), {
        seq: 1, actions: [],
    }));
});

test('a batch cannot be edited or deleted once written', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'mirror', 'u1');
    await env.withSecurityRulesDisabled(async (ctx) => {
        await setDoc(doc(ctx.firestore(), `participants/${CODE}/sessions/codoc/batches/b1`),
            { seq: 1, actions: [] });
    });
    await assertFails(setDoc(doc(db, `participants/${CODE}/sessions/codoc/batches/b1`),
        { seq: 1, actions: [{ t: 2, a: 'IDLE' }] }));
});

test('a device can save its own answers', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'browser', 'u1');
    await assertSucceeds(setDoc(doc(db, `participants/${CODE}/answers/background`),
        { yearsExperience: 7, agentUse: 'weekly' }));
});

test('a device cannot write the experimenter assessment', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'browser', 'u1');
    await assertFails(setDoc(doc(db, `participants/${CODE}/assessments/codoc`),
        { signoffConfidence: 5 }));
});

test('a device cannot change the participant document', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'browser', 'u1');
    await assertFails(setDoc(doc(db, `participants/${CODE}`), { released: true }));
});

test('the mirror can read its own slot to see whether it already registered', async () => {
    // It needs this on every restart to avoid consuming a second slot. The check
    // sits behind an OR with the experimenter test, and a rules evaluation error
    // in either side denies the whole expression rather than falling through.
    const db = anon('u1');
    await claim(db, CODE, 'mirror', 'u1');
    await assertSucceeds(getDoc(doc(db, `participants/${CODE}/devices/mirror`)));
});

test('a device cannot read a slot it does not hold', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'mirror', 'u1');
    await claim(anon('u2'), CODE, 'browser', 'u2');
    await assertFails(getDoc(doc(db, `participants/${CODE}/devices/browser`)));
});

// ── reading ──────────────────────────────────────────────────────────────────

test('a device cannot read another participant', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'browser', 'u1');
    await assertFails(getDoc(doc(db, `participants/${OTHER}`)));
});

test('a device cannot list the participants collection', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'browser', 'u1');
    await assertFails(getDocs(collection(db, 'participants')));
});

test('an unauthenticated visitor can read nothing', async () => {
    await assertFails(getDoc(doc(nobody(), `participants/${CODE}`)));
    await assertFails(getDocs(collection(nobody(), 'participants')));
});

test('a signed-in address that is not on the allowlist can read nothing', async () => {
    await assertFails(getDoc(doc(outsider(), `participants/${CODE}`)));
    await assertFails(getDocs(collection(outsider(), 'participants')));
});

test('an allowlisted experimenter can read every participant', async () => {
    await assertSucceeds(getDocs(collection(experimenter(), 'participants')));
    await assertSucceeds(getDoc(doc(experimenter(), `participants/${OTHER}`)));
});

test('an allowlisted experimenter can create a participant and write an assessment', async () => {
    const db = experimenter();
    await assertSucceeds(setDoc(doc(db, 'participants/p-qqqqqqqqqqqq'),
        { createdAt: Date.now(), order: 'baseline-first', released: false }));
    await assertSucceeds(setDoc(doc(db, `participants/${CODE}/assessments/codoc`),
        { signoffConfidence: 4, grounds: 'read the diff' }));
});

test('an unverified email on the allowlist is still refused', async () => {
    const db = env.authenticatedContext('exp2', { email: ALLOWED, email_verified: false }).firestore();
    await assertFails(getDoc(doc(db, `participants/${CODE}`)));
});

// ── the rule that keeps identifying fields out ───────────────────────────────

test('a device cannot write a document carrying a name or an email', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'browser', 'u1');
    await assertFails(setDoc(doc(db, `participants/${CODE}/answers/background`),
        { name: 'Alex Smith', yearsExperience: 7 }));
    await assertFails(setDoc(doc(db, `participants/${CODE}/answers/background`),
        { email: 'alex@example.com' }));
});

test('not even an experimenter can write an identifying field', async () => {
    // The guard is about where the data lives, not about who is trustworthy.
    // Consent stays in the Google Form; this database only ever holds a code.
    await assertFails(setDoc(doc(experimenter(), `participants/${CODE}/assessments/codoc`),
        { name: 'Alex Smith' }));
});

test('an action batch carrying an identifying field is refused', async () => {
    const db = anon('u1');
    await claim(db, CODE, 'mirror', 'u1');
    await assertFails(addDoc(collection(db, `participants/${CODE}/sessions/codoc/batches`),
        { seq: 1, actions: [], email: 'alex@example.com' }));
});
