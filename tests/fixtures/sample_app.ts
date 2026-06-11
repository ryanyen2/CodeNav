/**
 * Self-contained TypeScript fixture for the language-adapter tests.
 * Exercises: class + methods, exported/module-level arrow functions,
 * interface and type-alias declarations, plain const, and module-level code.
 */

import { EventEmitter } from "events";

export interface ClientOptions {
  uri: string;
  retries: number;
}

export type QueryResult = {
  rows: unknown[];
  elapsedMs: number;
};

const DEFAULT_RETRIES = 3;

export const makeOptions = (uri: string): ClientOptions => {
  return { uri, retries: DEFAULT_RETRIES };
};

function normalizeUri(uri: string): string {
  return uri.endsWith("/") ? uri.slice(0, -1) : uri;
}

export class Coordinator extends EventEmitter {
  private options: ClientOptions;
  private cache: Map<string, QueryResult>;

  constructor(options: ClientOptions) {
    super();
    this.options = options;
    this.cache = new Map();
  }

  connect(): Promise<void> {
    const uri = normalizeUri(this.options.uri);
    this.emit("connect", uri);
    return Promise.resolve();
  }

  query(sql: string): QueryResult {
    const cached = this.cache.get(sql);
    if (cached) {
      return cached;
    }
    const result: QueryResult = { rows: [], elapsedMs: 0 };
    this.cache.set(sql, result);
    return result;
  }

  clear(): void {
    this.cache.clear();
  }
}

export const sharedCoordinator = new Coordinator(makeOptions("memory://local"));
