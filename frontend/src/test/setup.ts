import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { resetResourceCache } from "../api/useResource";

// jsdom lacks layout and canvas; provide inert stand-ins so components run as in a
// browser that supports neither (the code paths already guard for this).
function installBrowserStubs(width = 1280) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  window.matchMedia = vi.fn((query: string) => {
    const max = /max-width:\s*(\d+)px/.exec(query);
    const min = /min-width:\s*(\d+)px/.exec(query);
    const matches = max ? window.innerWidth <= Number(max[1]) : min ? window.innerWidth >= Number(min[1]) : false;
    return {
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    } as unknown as MediaQueryList;
  });
}

beforeEach(() => {
  installBrowserStubs();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as unknown as HTMLCanvasElement["getContext"];
  Element.prototype.scrollIntoView = vi.fn();
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  resetResourceCache();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

export { installBrowserStubs };
