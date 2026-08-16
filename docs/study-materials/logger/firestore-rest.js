// The smallest Firestore client that does what the mirror needs.
//
// Deliberately not the Firebase SDK. The logger is one short file a participant
// can read before agreeing to run it, and it has no dependencies. Pulling in the
// SDK would cost both, and the mirror only ever does three things: sign in
// anonymously, refresh that sign-in, and create a document. All three are one
// HTTP call each, and fetch is built in.
//
// The API key here is the public web configuration. It identifies the project and
// grants nothing. The security rules are the protection.

const LIVE = Object.freeze({
    identity: 'https://identitytoolkit.googleapis.com/v1',
    secureToken: 'https://securetoken.googleapis.com/v1',
    firestore: 'https://firestore.googleapis.com/v1',
});

/**
 * The same three endpoints on a local emulator. Used by the integration test, so
 * that what is checked is the mirror talking to the real rules rather than the
 * mirror talking to a stub that agrees with it.
 */
export function emulatorHosts({ auth = '127.0.0.1:9099', firestore = '127.0.0.1:8080' } = {}) {
    return {
        identity: `http://${auth}/identitytoolkit.googleapis.com/v1`,
        secureToken: `http://${auth}/securetoken.googleapis.com/v1`,
        firestore: `http://${firestore}/v1`,
    };
}

/** Turn a plain value into Firestore's typed form. */
export function encode(value) {
    if (value === null || value === undefined) return { nullValue: null };
    if (typeof value === 'boolean') return { booleanValue: value };
    if (typeof value === 'number') {
        return Number.isInteger(value)
            ? { integerValue: String(value) }
            : { doubleValue: value };
    }
    if (typeof value === 'string') return { stringValue: value };
    if (Array.isArray(value)) {
        return { arrayValue: { values: value.map(encode) } };
    }
    if (typeof value === 'object') return { mapValue: { fields: encodeFields(value) } };
    return { stringValue: String(value) };
}

export function encodeFields(obj) {
    const fields = {};
    for (const [k, v] of Object.entries(obj || {})) {
        if (v === undefined) continue;
        fields[k] = encode(v);
    }
    return fields;
}

export class FirestoreRest {
    /**
     * @param {object} cfg  { apiKey, projectId }
     * @param {function} fetchImpl  injectable so the tests never touch the network
     */
    constructor(cfg, fetchImpl = globalThis.fetch) {
        this.apiKey = cfg.apiKey;
        this.projectId = cfg.projectId;
        this.hosts = cfg.hosts || LIVE;
        this.fetch = fetchImpl;
        this.idToken = null;
        this.refreshToken = null;
        this.uid = null;
        this.expiresAt = 0;
    }

    /** Restore a previous sign-in so a restart does not take a second slot. */
    restore({ refreshToken, uid }) {
        this.refreshToken = refreshToken || null;
        this.uid = uid || null;
    }

    async signIn() {
        // A cached refresh token is the whole reason a restart is cheap.
        if (this.refreshToken) {
            const r = await this.fetch(`${this.hosts.secureToken}/token?key=${this.apiKey}`, {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ grant_type: 'refresh_token', refresh_token: this.refreshToken }),
            });
            if (r.ok) {
                const d = await r.json();
                this.idToken = d.id_token;
                this.refreshToken = d.refresh_token || this.refreshToken;
                this.uid = d.user_id || this.uid;
                this.expiresAt = Date.now() + (Number(d.expires_in || 3600) - 60) * 1000;
                return { uid: this.uid, refreshToken: this.refreshToken };
            }
            // A refresh token that no longer works is not worth keeping.
            this.refreshToken = null;
        }
        const r = await this.fetch(`${this.hosts.identity}/accounts:signUp?key=${this.apiKey}`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ returnSecureToken: true }),
        });
        if (!r.ok) throw new Error(`anonymous sign-in failed (${r.status})`);
        const d = await r.json();
        this.idToken = d.idToken;
        this.refreshToken = d.refreshToken;
        this.uid = d.localId;
        this.expiresAt = Date.now() + (Number(d.expiresIn || 3600) - 60) * 1000;
        return { uid: this.uid, refreshToken: this.refreshToken };
    }

    async token() {
        if (!this.idToken || Date.now() >= this.expiresAt) await this.signIn();
        return this.idToken;
    }

    /**
     * Create one document at a known id.
     *
     * The id is chosen by the caller and derived from what is being sent, so a
     * resend after a crash lands on the same document. The rules allow create and
     * refuse update, so that resend comes back as "already exists" — which is the
     * answer we want and is treated as success. That is what makes the mirror
     * exactly-once rather than at-least-once.
     */
    async createDocument(collectionPath, documentId, data) {
        const token = await this.token();
        const url = `${this.hosts.firestore}/projects/${this.projectId}/databases/(default)/documents/`
            + `${collectionPath}?documentId=${encodeURIComponent(documentId)}`;
        const r = await this.fetch(url, {
            method: 'POST',
            headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
            body: JSON.stringify({ fields: encodeFields(data) }),
        });
        if (r.ok) return { created: true };
        if (r.status === 409) return { created: false, existed: true };
        const body = await safeText(r);
        throw new Error(`write failed (${r.status}) ${body.slice(0, 200)}`);
    }

    /** Read one document, or null when it is absent or not readable. */
    async getDocument(pathname) {
        const token = await this.token();
        const url = `${this.hosts.firestore}/projects/${this.projectId}`
            + `/databases/(default)/documents/${pathname}`;
        const r = await this.fetch(url, { headers: { authorization: `Bearer ${token}` } });
        if (!r.ok) return null;
        const d = await r.json();
        return decodeFields(d.fields || {});
    }
}

/** The inverse of encode, for the few values the mirror reads back. */
export function decodeFields(fields) {
    const out = {};
    for (const [k, v] of Object.entries(fields || {})) out[k] = decode(v);
    return out;
}

function decode(v) {
    if (!v || typeof v !== 'object') return v;
    if ('stringValue' in v) return v.stringValue;
    if ('integerValue' in v) return Number(v.integerValue);
    if ('doubleValue' in v) return v.doubleValue;
    if ('booleanValue' in v) return v.booleanValue;
    if ('nullValue' in v) return null;
    if ('arrayValue' in v) return (v.arrayValue.values || []).map(decode);
    if ('mapValue' in v) return decodeFields(v.mapValue.fields);
    return null;
}

async function safeText(r) {
    try { return await r.text(); } catch { return ''; }
}
