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

export interface StateResponse {
    stage: string;          // "uninit" | "needs-bootstrap" | "bootstrap-review" | "proposals-pending" | "stale-render" | "clean"
    pending_count: number;
    feature_count: number;
    head_hlc: string;
    base_hlc: string;
    hooks_installed: boolean;
    has_index: boolean;
    next_action: string;
}

export interface SyncRepoResponse {
    stage_before: string;
    stage_after: string;
    actions: string[];
    summary: string;
    pending_count: number;
    feature_count: number;
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

    /** Returns the repo state if the server is reachable, null otherwise. */
    async healthAndState(): Promise<StateResponse | null> {
        try {
            return await this.getState();
        } catch {
            return null;
        }
    }

    getState(): Promise<StateResponse> {
        return this.request('GET', `/state?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    syncRepo(opts: { accept_all?: boolean; prune_code?: boolean; from_ref?: string; to_ref?: string } = {}): Promise<SyncRepoResponse> {
        return this.request('POST', '/sync/repo', { root_dir: this.rootDir, ...opts });
    }

    listPending(): Promise<TransactionResponse[]> {
        return this.request('GET', `/tx/pending?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    getTree(rootUuid?: string): Promise<FeatureResponse[]> {
        const q = rootUuid ? `&root_uuid=${rootUuid}` : '';
        return this.request('GET', `/tree?root_dir=${encodeURIComponent(this.rootDir)}${q}`);
    }

    getFeatureBindings(uuid: string): Promise<BindingResponse[]> {
        return this.request('GET', `/feature/${uuid}/bindings?root_dir=${encodeURIComponent(this.rootDir)}`);
    }

    resolveAnchor(file: string, symbolPath: string | null, tsQuery: string | null, occurrenceIndex: number): Promise<{ start_line: number; end_line: number; start_byte: number; end_byte: number } | null> {
        return this.request('POST', '/anchor/resolve', {
            root_dir: this.rootDir,
            file,
            symbol_path: symbolPath,
            ts_query: tsQuery,
            occurrence_index: occurrenceIndex,
        });
    }

    getBindingsByFile(file: string): Promise<Record<string, string>> {
        return this.request('GET', `/bindings/by-file?file=${encodeURIComponent(file)}&root_dir=${encodeURIComponent(this.rootDir)}`);
    }

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
