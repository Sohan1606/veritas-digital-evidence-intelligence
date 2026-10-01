import { describe, expect, it, vi } from "vitest";
import { ApiError, apiDownload, apiGet, apiMutation, apiUpload } from "../api/client";

const respond = (status: number, body: string, type = "application/json") =>
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status, headers: { "content-type": type, "x-request-id": "req-1" } })));

describe("API client", () => {
  it("refuses absolute URLs so the browser never addresses another host", async () => {
    await expect(apiGet("https://example.org/api")).rejects.toThrow(/relative/);
  });

  it.each([
    ["https://example.org/api"],
    ["//example.org/api"],
    ["/\\example.org/api"],
    ["/\t/example.org/api"],
    ["/\n/example.org/api"],
    ["/ api"],
    ["api/v1/cases"],
    [""],
  ])("every client function refuses %j, including protocol-relative forms", async (path) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const body = new Blob(["x"]);
    for (const call of [
      () => apiGet(path),
      () => apiMutation("POST", path),
      () => apiUpload(path, body),
      () => apiDownload(path),
    ]) {
      await expect(call()).rejects.toThrow(/relative/);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still accepts every ordinary same-origin API path", async () => {
    respond(200, "{}");
    await expect(apiGet("/api/v1/cases/CASE-001/evidence/EVD-001/intake")).resolves.toEqual({});
  });

  it.each([
    [401, "restricted"],
    [404, "not_found"],
    [422, "invalid"],
    [503, "unreachable"],
    [500, "server"],
  ] as const)("classifies HTTP %i as %s and keeps the request id", async (status, kind) => {
    respond(status, JSON.stringify({ error: { code: "x", message: "m", request_id: "req-9" } }));
    const error = await apiGet("/api/v1/cases").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe(kind);
    expect((error as ApiError).requestId).toBe("req-9");
  });

  it("treats a non-JSON success (e.g. an HTML fallback) as unreachable", async () => {
    respond(200, "<!doctype html>", "text/html");
    await expect(apiGet("/api/v1/cases")).rejects.toMatchObject({ kind: "unreachable" });
  });

  it("sends evidence as a raw same-origin body with the CSRF token", async () => {
    document.cookie = "veritas_csrf=synthetic-csrf";
    const content = new Blob(["synthetic raw bytes"], { type: "application/pdf" });
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const path = "/api/v1/cases/CASE-001/evidence/EVD-001/objects/EOBJ-001/content";
    await expect(apiUpload(path, content)).resolves.toEqual({});
    expect(fetchMock).toHaveBeenCalledWith(path, expect.objectContaining({
      method: "PUT",
      credentials: "same-origin",
      body: content,
      headers: expect.objectContaining({
        "Content-Type": "application/octet-stream",
        "X-CSRF-Token": "synthetic-csrf",
      }),
    }));
    document.cookie = "veritas_csrf=; Max-Age=0";
  });
});

describe("apiDownload", () => {
  const path = "/api/v1/cases/CASE-001/evidence/EVD-001/objects/EOBJ-001/content";

  it("returns the bytes untouched with the backend's filename and sends credentials only", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response("raw \u0000\u0001 bytes", {
          status: 200,
          headers: { "content-type": "application/pdf", "content-disposition": 'attachment; filename="EOBJ-001.bin"' },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { blob, filename } = await apiDownload(path);
    expect(await blob.text()).toBe("raw \u0000\u0001 bytes");
    expect(filename).toBe("EOBJ-001.bin");
    // A plain same-origin GET: no body, no JSON negotiation, no caller-controlled headers.
    expect(fetchMock).toHaveBeenCalledWith(path, { credentials: "same-origin" });
  });

  it.each([
    ['attachment; filename="../../etc/passwd"'],
    ['attachment; filename="a\\b.bin"'],
    ['attachment; filename=""'],
    ['attachment; filename="-hidden.bin"'],
    ["attachment"],
  ])("ignores an unsafe or missing Content-Disposition (%s)", async (disposition) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("x", { status: 200, headers: { "content-disposition": disposition } })));
    expect((await apiDownload(path)).filename).toBeNull();
  });

  it("classifies a failure through the structured envelope and keeps the request id", async () => {
    respond(503, JSON.stringify({ error: { code: "evidence_storage_unavailable", message: "Private evidence storage is unavailable", request_id: "req-7" } }));
    const error = await apiDownload(path).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "unreachable", status: 503, code: "evidence_storage_unavailable", requestId: "req-7" });
    expect((error as ApiError).message).toBe("Private evidence storage is unavailable");
  });

  it.each([
    [401, "restricted"],
    [404, "not_found"],
    [409, "server"],
  ] as const)("classifies HTTP %i as %s", async (status, kind) => {
    respond(status, JSON.stringify({ error: { code: "x", message: "m", request_id: "req-1" } }));
    await expect(apiDownload(path)).rejects.toMatchObject({ kind, status });
  });

  it("falls back to the response header id when a failure has no JSON body", async () => {
    respond(502, "<html>Bad gateway</html>", "text/html");
    await expect(apiDownload(path)).rejects.toMatchObject({ kind: "unreachable", requestId: "req-1" });
  });

  it("maps a network failure to an unreachable error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(apiDownload(path)).rejects.toMatchObject({ kind: "unreachable", status: null });
  });
});
