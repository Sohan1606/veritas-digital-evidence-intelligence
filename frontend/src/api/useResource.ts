import { useCallback, useEffect, useSyncExternalStore } from "react";
import { ApiError, apiGet } from "./client";

/**
 * Read-only resource cache shared by every view, so the sidebar, command palette and
 * pages reuse one request per path. V1 data is read-only, so entries never go stale
 * within a session; `reload()` exists for error recovery.
 */

export type ResourceState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: ApiError };

type Entry = { state: ResourceState<unknown>; promise?: Promise<void> };

const cache = new Map<string, Entry>();
const listeners = new Set<() => void>();
let version = 0;

function notify() {
  version += 1;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function toApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError("server", "Unexpected client error.", null, null, null);
}

function load(path: string) {
  const existing = cache.get(path);
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
      notify();
    });
  cache.set(path, entry);
}

/** Subscribe to a GET resource. Pass `null` to skip fetching. */
export function useResource<T>(path: string | null): ResourceState<T> & { reload: () => void } {
  useSyncExternalStore(subscribe, () => version);
  useEffect(() => {
    if (path) load(path);
  }, [path]);

  const reload = useCallback(() => {
    if (!path) return;
    cache.delete(path);
    load(path);
    notify();
  }, [path]);

  if (!path) return { status: "loading", reload };
  const state = (cache.get(path)?.state ?? { status: "loading" }) as ResourceState<T>;
  return { ...state, reload };
}

/** Test helper: clear all cached resources. */
export function resetResourceCache() {
  cache.clear();
  notify();
}
