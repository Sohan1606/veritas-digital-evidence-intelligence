import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { ApiError, apiGet } from "./client";

/**
 * GET resources are cached only inside the current authenticated session boundary.
 * Session transitions clear the cache, so no protected response can be reused by
 * another principal while pages within an unchanged session still share requests.
 */

export type ResourceState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: ApiError };

type Entry = {
  state: ResourceState<unknown>;
  promise?: Promise<void>;
  /** The last in-place refresh failed while the previous data is still shown. */
  refreshError?: ApiError;
  /** A refresh was requested while one was in flight; run exactly one more afterwards. */
  again?: boolean;
};

const cache = new Map<string, Entry>();
const listeners = new Set<() => void>();
let version = 0;
let identityScope = "anonymous";

function notify() {
  version += 1;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function cacheKey(scope: string, path: string): string {
  return `${scope}\u0000${path}`;
}

function toApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError("server", "Unexpected client error.", null, null, null);
}

function load(path: string, scope: string) {
  const key = cacheKey(scope, path);
  const existing = cache.get(key);
  if (existing && (existing.promise || existing.state.status === "ready")) return;
  const entry: Entry = { state: { status: "loading" } };
  entry.promise = apiGet<unknown>(path)
    .then((data) => {
      entry.state = { status: "ready", data };
    })
    .catch((error: unknown) => {
      entry.state = { status: "error", error: toApiError(error) };
    })
    .finally(() => {
      entry.promise = undefined;
      // An old in-flight request may finish after an identity switch. It only mutates
      // its detached Entry; the new scope has a different key and cannot read it.
      notify();
    });
  cache.set(key, entry);
}

/**
 * Refresh a loaded resource in place: the previous data stays on screen until the new data
 * arrives (no loading flicker). A failure keeps the previous data and records ``refreshError`` so
 * a caller can say plainly that what is shown may be out of date.
 */
function revalidate(path: string, scope: string) {
  const entry = cache.get(cacheKey(scope, path));
  if (!entry) return;
  if (entry.promise) {
    entry.again = true; // the request in flight may predate the change that asked for this
    return;
  }
  entry.promise = apiGet<unknown>(path)
    .then((data) => {
      entry.state = { status: "ready", data };
      entry.refreshError = undefined;
    })
    .catch((error: unknown) => {
      const failure = toApiError(error);
      if (entry.state.status === "ready") entry.refreshError = failure;
      else entry.state = { status: "error", error: failure };
    })
    .finally(() => {
      entry.promise = undefined;
      notify();
      if (entry.again) {
        entry.again = false;
        revalidate(path, scope);
      }
    });
}

export interface ResourceOptions<T> {
  /** Re-fetch in place at this interval (only while ``refreshWhile`` allows it). */
  refreshMs?: number;
  /** Keep refreshing only while this is true for the latest data (e.g. a run is still active). */
  refreshWhile?: (data: T) => boolean;
}

/** Replace the identity boundary and drop every cached/in-flight resource reference. */
export function setResourceIdentityScope(scope: string) {
  if (scope === identityScope) return;
  identityScope = scope;
  cache.clear();
  notify();
}

/** Subscribe to a GET resource. Pass `null` to skip fetching. */
export function useResource<T>(
  path: string | null,
  options: ResourceOptions<T> = {},
): ResourceState<T> & { reload: () => void; refresh: () => void; refreshError: ApiError | null } {
  useSyncExternalStore(subscribe, () => version);
  const scope = identityScope;
  const key = path ? cacheKey(scope, path) : null;
  const { refreshMs } = options;
  const refreshWhile = useRef(options.refreshWhile);
  useEffect(() => {
    refreshWhile.current = options.refreshWhile;
  });
  useEffect(() => {
    if (path) load(path, scope);
  }, [path, scope]);

  useEffect(() => {
    if (!path || !refreshMs) return;
    const timer = window.setInterval(() => {
      const state = cache.get(cacheKey(scope, path))?.state;
      if (state?.status !== "ready") return;
      if (refreshWhile.current && !refreshWhile.current(state.data as T)) return;
      revalidate(path, scope);
    }, refreshMs);
    return () => window.clearInterval(timer);
  }, [path, scope, refreshMs]);

  const reload = useCallback(() => {
    if (!path || !key) return;
    cache.delete(key);
    load(path, scope);
    notify();
  }, [path, key, scope]);

  const refresh = useCallback(() => {
    if (path) revalidate(path, scope);
  }, [path, scope]);

  if (!path || !key) return { status: "loading", reload, refresh, refreshError: null };
  const entry = cache.get(key);
  const state = (entry?.state ?? { status: "loading" }) as ResourceState<T>;
  return { ...state, reload, refresh, refreshError: entry?.refreshError ?? null };
}

/** Test helper: reset cache and return to an isolated anonymous scope. */
export function resetResourceCache() {
  cache.clear();
  identityScope = "anonymous";
  notify();
}
