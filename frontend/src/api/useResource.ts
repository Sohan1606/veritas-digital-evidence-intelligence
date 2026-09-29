import { useCallback, useEffect, useSyncExternalStore } from "react";
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

type Entry = { state: ResourceState<unknown>; promise?: Promise<void> };

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

/** Replace the identity boundary and drop every cached/in-flight resource reference. */
export function setResourceIdentityScope(scope: string) {
  if (scope === identityScope) return;
  identityScope = scope;
  cache.clear();
  notify();
}

/** Subscribe to a GET resource. Pass `null` to skip fetching. */
export function useResource<T>(path: string | null): ResourceState<T> & { reload: () => void } {
  useSyncExternalStore(subscribe, () => version);
  const scope = identityScope;
  const key = path ? cacheKey(scope, path) : null;
  useEffect(() => {
    if (path) load(path, scope);
  }, [path, scope]);

  const reload = useCallback(() => {
    if (!path || !key) return;
    cache.delete(key);
    load(path, scope);
    notify();
  }, [path, key, scope]);

  if (!path || !key) return { status: "loading", reload };
  const state = (cache.get(key)?.state ?? { status: "loading" }) as ResourceState<T>;
  return { ...state, reload };
}

/** Test helper: reset cache and return to an isolated anonymous scope. */
export function resetResourceCache() {
  cache.clear();
  identityScope = "anonymous";
  notify();
}
