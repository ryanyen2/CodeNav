// HTTP client for the codoc FastAPI server.
// All calls are made from the extension host (not the webview) to avoid CORS issues.

export interface TransactionResponse {
    hlc: string;
    kind: string;
    payload: Record<string, unknown>;
    author: string;
    proposal: boolean;
    accepted_at: string | null;
    label: string | null;
}

export interface FeatureResponse {
    uuid: string;
    slug: string;
    parent_uuid: string | null;
    intent: string;
    retired: boolean;
    state: string;
    binding_count: number;
}

export interface BindingResponse {
    uuid: string;
    feature_uuid: string;
    anchor: {
        file: string;
        symbol_path: string | null;
        ts_query: string | null;
        occurrence_index: number;
    };
    fingerprint: string;
}

export interface AnchorPosition {
    start_line: number;
    end_line: number;
    start_byte: number;
    end_byte: number;
}

export class CodocClient {
    constructor(private baseUrl: string, private rootDir: string) {}

    private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
        const url = `${this.baseUrl}${path}`;
        const resp = await fetch(url, {
            method,
            headers: body ? { 'Content-Type': 'application/json' } : {},
            body: body ? JSON.stringify(body) : undefined,
        });
        if (!resp.ok) {
            const text = await resp.text().catch(() => resp.statusText);
            throw new Error(`codoc API ${method} ${path} → ${resp.status}: ${text}`);
        }
        return resp.json() as Promise<T>;
    }

    async health(): Promise<boolean> {
        try {
            await fetch(`${this.baseUrl}/status?root_dir=${encodeURIComponent(this.rootDir)}`);
            return true;
        } catch {
            return false;
        }
    }

    listPending(): Promise<TransactionResponse[]> {
        return this.request('GET', `/tx/pending?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    acceptTx(hlc: string, edits?: Record<string, unknown>): Promise<TransactionResponse> {
        return this.request('POST', `/tx/${encodeURIComponent(hlc)}/accept`, { root_dir: this.rootDir, edits });
    }

    rejectTx(hlc: string): Promise<void> {
        return this.request('POST', `/tx/${encodeURIComponent(hlc)}/reject`, { root_dir: this.rootDir });
    }

    labelTx(hlc: string, label: string): Promise<TransactionResponse> {
        return this.request('POST', `/tx/${encodeURIComponent(hlc)}/label`, { root_dir: this.rootDir, label });
    }

    getTree(rootUuid?: string): Promise<FeatureResponse[]> {
        const q = rootUuid ? `&root_uuid=${rootUuid}` : '';
        return this.request('GET', `/tree?root_dir=${encodeURIComponent(this.rootDir)}${q}`);
    }

    getFeature(uuid: string): Promise<FeatureResponse> {
        return this.request('GET', `/feature/${uuid}?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    getFeatureBindings(uuid: string): Promise<BindingResponse[]> {
        return this.request('GET', `/feature/${uuid}/bindings?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    getFeatureHistory(uuid: string): Promise<TransactionResponse[]> {
        return this.request('GET', `/feature/${uuid}/history?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    resolveAnchor(file: string, symbolPath: string | null, tsQuery: string | null, occurrenceIndex: number): Promise<AnchorPosition | null> {
        return this.request('POST', '/anchor/resolve', {
            root_dir: this.rootDir,
            file,
            symbol_path: symbolPath,
            ts_query: tsQuery,
            occurrence_index: occurrenceIndex,
        });
    }

    reflect(fromRef = 'HEAD~1', toRef = 'HEAD'): Promise<unknown> {
        return this.request('POST', '/reflect', { root_dir: this.rootDir, from_ref: fromRef, to_ref: toRef });
    }

    bootstrap(targetClusterSize = 8): Promise<unknown> {
        return this.request('POST', '/bootstrap', { root_dir: this.rootDir, target_cluster_size: targetClusterSize });
    }

    amend(featureUuid: string, newIntent: string): Promise<TransactionResponse> {
        return this.request('POST', '/tx/intentional/amend', { root_dir: this.rootDir, feature_uuid: featureUuid, new_intent: newIntent });
    }

    rename(featureUuid: string, newSlug: string): Promise<TransactionResponse> {
        return this.request('POST', '/tx/intentional/rename', { root_dir: this.rootDir, feature_uuid: featureUuid, new_slug: newSlug });
    }

    retire(featureUuid: string): Promise<TransactionResponse> {
        return this.request('POST', '/tx/intentional/retire', { root_dir: this.rootDir, feature_uuid: featureUuid });
    }

    // ----- Repo lifecycle endpoints -----

    async initRepo(): Promise<void> {
        await fetch(`${this.baseUrl}/init`, { method: 'POST' });
    }

    async getRepoState(): Promise<{ hasIndex: boolean; isStale: boolean; pendingCount: number }> {
        const r = await fetch(`${this.baseUrl}/state?root_dir=${encodeURIComponent(this.rootDir)}`);
        return r.json() as Promise<{ hasIndex: boolean; isStale: boolean; pendingCount: number }>;
    }

    async getBindingsByFile(file: string): Promise<Record<string, string>> {
        const r = await fetch(`${this.baseUrl}/bindings/by-file?file=${encodeURIComponent(file)}&root_dir=${encodeURIComponent(this.rootDir)}`);
        if (!r.ok) return {};
        return r.json() as Promise<Record<string, string>>;
    }

    // ----- Phase 1.5 projection endpoints -----

    syncFile(author = 'user'): Promise<SyncResponse> {
        return this.request('POST', '/sync', { root_dir: this.rootDir, author });
    }

    renderTree(): Promise<RenderResponse> {
        return this.request('GET', `/tree.codoc?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    acceptProposal(hlc: string, edits?: Record<string, unknown>): Promise<TransactionResponse> {
        return this.request('POST', `/tx/${encodeURIComponent(hlc)}/accept`, { root_dir: this.rootDir, edits });
    }

    rejectProposal(hlc: string): Promise<void> {
        return this.request('POST', `/tx/${encodeURIComponent(hlc)}/reject`, { root_dir: this.rootDir });
    }

    acceptAll(label?: string): Promise<{ accepted: number; failed: Array<{hlc: string; error: string}> }> {
        return this.request('POST', '/tx/accept-all', { root_dir: this.rootDir, label });
    }

    rejectAll(): Promise<{ rejected: number; failed: Array<{hlc: string; error: string}> }> {
        return this.request('POST', '/tx/reject-all', { root_dir: this.rootDir });
    }

    // ----- Claude Code live activity -----

    getActivity(): Promise<Array<{
        session_id: string;
        rel_path: string;
        tool: string;
        started_at: number;
        feature_uuids: string[];
        feature_slugs: string[];
    }>> {
        return this.request('GET', `/claude-code/activity?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    subscribeToEvents(
        onEvent: (topic: string, data: unknown) => void,
        onError: () => void,
    ): () => void {
        const url = `${this.baseUrl}/events/stream?root_dir=${encodeURIComponent(this.rootDir)}`;
        let closed = false;

        const connect = () => {
            if (closed) return;
            const controller = new AbortController();
            fetch(url, { signal: controller.signal })
                .then(async (resp) => {
                    if (!resp.ok || !resp.body) { onError(); return; }
                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder();
                    let buf = '';
                    let currentEvent = '';
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buf += decoder.decode(value, { stream: true });
                        const lines = buf.split('\n');
                        buf = lines.pop() ?? '';
                        for (const line of lines) {
                            if (line.startsWith('event:')) {
                                currentEvent = line.slice(6).trim();
                            } else if (line.startsWith('data:')) {
                                try {
                                    const data = JSON.parse(line.slice(5).trim());
                                    onEvent(currentEvent || 'message', data);
                                } catch { /* ignore malformed */ }
                                currentEvent = '';
                            }
                        }
                    }
                    // Stream ended — reconnect after 2s.
                    if (!closed) setTimeout(connect, 2000);
                })
                .catch(() => {
                    if (!closed) {
                        onError();
                        setTimeout(connect, 5000);
                    }
                });

            return () => { controller.abort(); };
        };

        const cancel = connect();
        return () => {
            closed = true;
            cancel?.();
        };
    }
}

export interface SyncResponse {
    applied: string[];
    errors: Array<{ kind: string; message: string; file: string | null; line: number | null }>;
    status: 'ok' | 'stale_buffer' | 'parse_error' | 'partial' | 'noop';
    files: Record<string, string> | null;
}

export interface RenderResponse {
    files: Record<string, string>;
    base_hlc: string;
}
