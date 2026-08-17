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
import { doc, getDoc, setDoc, deleteDoc, collection, getDocs, addDoc } from 'firebase/firestore';

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

// ── who a code belongs to ────────────────────────────────────────────────────

test('a participant cannot read the contact record for their own code', async () => {
    // The code is the credential for everything else they touch, and this is
    // the one thing it must not open. A name is the whole of what separates a
    // session log from a person.
    const them = anon('someone');
    await assertFails(getDoc(doc(them, 'contacts/p-abcdefghjkmn')));
});

test('a signed-out stranger cannot read one either', async () => {
    await assertFails(getDoc(doc(nobody(), 'contacts/p-abcdefghjkmn')));
});

test('the experimenter can keep a name and an email there', async () => {
    // The one place identifying detail is allowed, and it is a separate
    // collection so that exporting a session cannot pick it up by accident.
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, 'contacts/p-abcdefghjkmn'),
        { name: 'A Person', email: 'a@example.com', note: 'booked Tuesday' }));
});

test('a name is still refused on the participant document itself', async () => {
    // Where it would travel with the session data by default.
    const me = experimenter();
    await assertFails(setDoc(doc(me, 'participants/p-abcdefghjkmn'),
        { createdAt: 1, order: 'codoc-first', name: 'A Person' }));
});

test('only the experimenter can delete a participant', async () => {
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, 'participants/p-todelete000'),
        { createdAt: 1, order: 'codoc-first', released: false }));
    const them = anon('someone-else');
    await assertFails(deleteDoc(doc(them, 'participants/p-todelete000')));
    await assertSucceeds(deleteDoc(doc(me, 'participants/p-todelete000')));
});

// ── the keys, fetched rather than pasted ─────────────────────────────────────

test('a registered device can read its own copy of the keys', async () => {
    // This is what removes the pasting step. A key copied by hand is a key that
    // ends up in the wrong window, and the copying is the step that fails on a
    // call.
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, 'participants/p-abcdefghjkmn'),
        { createdAt: 1, order: 'codoc-first', released: false }));
    await assertSucceeds(setDoc(doc(me, 'participants/p-abcdefghjkmn/secrets/session'),
        { anthropicApiKey: 'sk-ant-x', openaiApiKey: 'sk-y' }));

    const them = anon('their-machine');
    await claim(them, 'p-abcdefghjkmn', 'mirror', 'their-machine');
    await assertSucceeds(getDoc(doc(them, 'participants/p-abcdefghjkmn/secrets/session')));
});

// The path setup.sh actually walks. Written out separately because every test
// above claims `mirror`, and the rules once allowed only the two writer slots
// while the script took a third called `setup` — so the suite was green and
// every participant's key fetch was denied.
test('the setup script takes its own slot and reads the keys', async () => {
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, `participants/${CODE}/secrets/session`),
        { anthropicApiKey: 'sk-ant-x', openaiApiKey: 'sk-y' }));

    const run = anon('setup-run-1');
    await assertSucceeds(claim(run, CODE, 'setup', 'setup-run-1'));
    const got = await assertSucceeds(getDoc(doc(run, `participants/${CODE}/secrets/session`)));
    assert.equal(got.data().anthropicApiKey, 'sk-ant-x');
});

test('running setup again takes the slot back and still reads the keys', async () => {
    // Setup keeps no account between runs, so a second run arrives as a
    // different anonymous uid. Claim-once here would mean setup works exactly
    // once per participant, which is the run before anybody needs it to work.
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, `participants/${CODE}/secrets/session`),
        { anthropicApiKey: 'sk-ant-x' }));

    await assertSucceeds(claim(anon('setup-run-1'), CODE, 'setup', 'setup-run-1'));
    const again = anon('setup-run-2');
    await assertSucceeds(claim(again, CODE, 'setup', 'setup-run-2'));
    await assertSucceeds(getDoc(doc(again, `participants/${CODE}/secrets/session`)));
    // And the run that lost the slot loses the keys with it.
    await assertFails(getDoc(doc(anon('setup-run-1'), `participants/${CODE}/secrets/session`)));
});

test('the setup slot cannot be taken once the code is released', async () => {
    await env.withSecurityRulesDisabled(async (ctx) => {
        await setDoc(doc(ctx.firestore(), `participants/${CODE}`), {
            createdAt: Date.now(), order: 'codoc-first', released: true,
        });
    });
    await assertFails(claim(anon('setup-run-1'), CODE, 'setup', 'setup-run-1'));
});

test('the setup slot reads the keys and writes nothing else', async () => {
    // It is a fetch, not a third writer. Session data still comes from the two.
    const run = anon('setup-run-1');
    await assertSucceeds(claim(run, CODE, 'setup', 'setup-run-1'));
    await assertFails(setDoc(doc(run, `participants/${CODE}/answers/questionnaire`),
        { q1: 3 }));
    await assertFails(setDoc(doc(run, `participants/${CODE}/sessions/codoc`),
        { startedAt: 1 }));
    await assertFails(setDoc(doc(run, `participants/${CODE}/secrets/session`),
        { anthropicApiKey: 'sk-ant-mine' }));
});

test('holding the setup slot on one code reads no other code\'s keys', async () => {
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, `participants/${OTHER}/secrets/session`),
        { anthropicApiKey: 'sk-ant-theirs' }));
    const run = anon('setup-run-1');
    await assertSucceeds(claim(run, CODE, 'setup', 'setup-run-1'));
    await assertFails(getDoc(doc(run, `participants/${OTHER}/secrets/session`)));
});

test('somebody who has not registered a device cannot read them', async () => {
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, 'participants/p-qqqqqqqqqqqq'),
        { createdAt: 1, order: 'codoc-first', released: false }));
    await assertSucceeds(setDoc(doc(me, 'participants/p-qqqqqqqqqqqq/secrets/session'),
        { anthropicApiKey: 'sk-ant-x' }));
    const stranger = anon('some-stranger');
    await assertFails(getDoc(doc(stranger, 'participants/p-qqqqqqqqqqqq/secrets/session')));
});

test('a participant cannot read another participant\'s keys', async () => {
    const me = experimenter();
    for (const code of ['p-aaaaaaaaaaaa', 'p-bbbbbbbbbbbb']) {
        await assertSucceeds(setDoc(doc(me, `participants/${code}`),
            { createdAt: 1, order: 'codoc-first', released: false }));
        await assertSucceeds(setDoc(doc(me, `participants/${code}/secrets/session`),
            { anthropicApiKey: 'sk-ant-x' }));
    }
    const theirs = anon('machine-a');
    await claim(theirs, 'p-aaaaaaaaaaaa', 'mirror', 'machine-a');
    await assertFails(getDoc(doc(theirs, 'participants/p-bbbbbbbbbbbb/secrets/session')));
});

test('a device cannot write over the keys it was issued', async () => {
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, 'participants/p-cccccccccccc'),
        { createdAt: 1, order: 'codoc-first', released: false }));
    const them = anon('machine-c');
    await claim(them, 'p-cccccccccccc', 'mirror', 'machine-c');
    await assertFails(setDoc(doc(them, 'participants/p-cccccccccccc/secrets/session'),
        { anthropicApiKey: 'sk-ant-mine' }));
});

test('the keys the experimenter types once are theirs alone to read', async () => {
    const me = experimenter();
    await assertSucceeds(setDoc(doc(me, 'settings/keys'),
        { anthropicApiKey: 'sk-ant-x', openaiApiKey: 'sk-y' }));
    await assertFails(getDoc(doc(anon('anyone'), 'settings/keys')));
    await assertFails(getDoc(doc(outsider(), 'settings/keys')));
});
