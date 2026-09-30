import { describe, expect, it, vi } from "vitest";
import { ApiError, apiGet, apiUpload } from "../api/client";

const respond = (status: number, body: string, type = "application/json") =>
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status, headers: { "content-type": type, "x-request-id": "req-1" } })));

describe("API client", () => {
  it("refuses absolute URLs so the browser never addresses another host", async () => {
    await expect(apiGet("https://example.org/api")).rejects.toThrow(/relative/);
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
