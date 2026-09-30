/**
 * Minimal API client. Relative URLs only: in development Vite proxies them to the
 * backend; in deployment the reverse proxy does. The browser never addresses the API host.
 */

export type ApiFailureKind = "unreachable" | "restricted" | "forbidden" | "not_found" | "invalid" | "server";

export class ApiError extends Error {
  readonly kind: ApiFailureKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(kind: ApiFailureKind, message: string, status: number | null, code: string | null, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string | null };
}

function classify(status: number): ApiFailureKind {
  if (status === 401) return "restricted";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 400 || status === 413 || status === 415 || status === 422) return "invalid";
  if (status === 502 || status === 503 || status === 504) return "unreachable";
  return "server";
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  if (!path.startsWith("/")) throw new Error("API paths must be relative to the current origin");
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" }, signal, credentials: "same-origin" });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError("unreachable", "The VERITAS API could not be reached.", null, null, null);
  }
  const requestId = response.headers.get("x-request-id");
  const isJson = (response.headers.get("content-type") ?? "").includes("application/json");
  if (!response.ok) {
    const body: ErrorEnvelope = isJson ? await response.json().catch(() => ({})) : {};
    return Promise.reject(
      new ApiError(
        classify(response.status),
        body.error?.message ?? `Request failed (${response.status}).`,
        response.status,
        body.error?.code ?? null,
        body.error?.request_id ?? requestId,
      ),
    );
  }
  if (!isJson) {
    // e.g. an HTML fallback page served because the API is not behind this origin.
    throw new ApiError("unreachable", "The VERITAS API did not return JSON.", response.status, null, requestId);
  }
  return (await response.json()) as T;
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const value = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : null;
}

export async function apiMutation<T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  if (!path.startsWith("/")) throw new Error("API paths must be relative to the current origin");
  let response: Response;
  try {
    const csrf = readCookie("veritas_csrf");
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new ApiError("unreachable", "The VERITAS API could not be reached.", null, null, null);
  }
  const requestId = response.headers.get("x-request-id");
  const isJson = (response.headers.get("content-type") ?? "").includes("application/json");
  const payload: ErrorEnvelope = isJson ? await response.json().catch(() => ({})) : {};
  if (!response.ok) {
    throw new ApiError(
      classify(response.status),
      payload.error?.message ?? `Request failed (${response.status}).`,
      response.status,
      payload.error?.code ?? null,
      payload.error?.request_id ?? requestId,
    );
  }
  return payload as T;
}

/** Raw, same-origin binary upload. Evidence bytes are never JSON-encoded or logged here. */
export async function apiUpload<T>(path: string, content: Blob): Promise<T> {
  if (!path.startsWith("/")) throw new Error("API paths must be relative to the current origin");
  let response: Response;
  try {
    const csrf = readCookie("veritas_csrf");
    response = await fetch(path, {
      method: "PUT",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/octet-stream",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: content,
    });
  } catch {
    throw new ApiError("unreachable", "The VERITAS API could not be reached.", null, null, null);
  }
  const requestId = response.headers.get("x-request-id");
  const isJson = (response.headers.get("content-type") ?? "").includes("application/json");
  const payload: ErrorEnvelope = isJson ? await response.json().catch(() => ({})) : {};
  if (!response.ok) {
    throw new ApiError(
      classify(response.status),
      payload.error?.message ?? `Request failed (${response.status}).`,
      response.status,
      payload.error?.code ?? null,
      payload.error?.request_id ?? requestId,
    );
  }
  return payload as T;
}
