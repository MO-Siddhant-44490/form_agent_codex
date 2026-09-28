import { describe, expect, it, vi } from "vitest";
import { isVisible } from "../../src/perception/visibility";

function mountInput(wrapper: (input: string) => string): HTMLElement {
  document.body.innerHTML = wrapper(`<input id="target">`);
  return document.getElementById("target")!;
}

describe("visibility and honeypot filtering (threat T2)", () => {
  it("plain input is visible", () => {
    expect(isVisible(mountInput((i) => i))).toBe(true);
  });

  it("hidden attribute", () => {
    document.body.innerHTML = `<input id="target" hidden>`;
    expect(isVisible(document.getElementById("target")!)).toBe(false);
  });

  it("input type=hidden", () => {
    document.body.innerHTML = `<input id="target" type="hidden">`;
    expect(isVisible(document.getElementById("target")!)).toBe(false);
  });

  it("display:none on self", () => {
    document.body.innerHTML = `<input id="target" style="display:none">`;
    expect(isVisible(document.getElementById("target")!)).toBe(false);
  });

  it("visibility:hidden on ancestor", () => {
    expect(isVisible(mountInput((i) => `<div style="visibility:hidden">${i}</div>`))).toBe(false);
  });

  it("aria-hidden ancestor", () => {
    expect(isVisible(mountInput((i) => `<div aria-hidden="true">${i}</div>`))).toBe(false);
  });

  it("offscreen honeypot wrapper (position:absolute; left:-9999px)", () => {
    expect(
      isVisible(mountInput((i) => `<div style="position:absolute;left:-9999px;top:-9999px">${i}</div>`)),
    ).toBe(false);
  });

  it("offscreen honeypot on the input itself", () => {
    document.body.innerHTML = `<input id="target" style="position:absolute;top:-5000px">`;
    expect(isVisible(document.getElementById("target")!)).toBe(false);
  });
});

describe("scrolled pages (layout geometry)", () => {
  function withLayout(elRect: Partial<DOMRect>, scrollY: number, run: () => void): void {
    const rect = (r: Partial<DOMRect>) => ({ x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, toJSON() {}, ...r }) as DOMRect;
    const docSpy = vi.spyOn(document.documentElement, "getBoundingClientRect").mockReturnValue(rect({ width: 1280, right: 1280 }));
    const elSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect(elRect));
    Object.defineProperty(window, "scrollY", { value: scrollY, configurable: true });
    try {
      run();
    } finally {
      docSpy.mockRestore();
      elSpy.mockRestore();
      Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
    }
  }

  it("a field the user has scrolled past (negative viewport y) is still visible", () => {
    document.body.innerHTML = `<input id="f" type="text">`;
    // 3000 px scrolled; the field sits at document y=250 -> viewport y=-2750.
    withLayout({ x: 140, y: -2750, top: -2750, bottom: -2710, left: 140, right: 580, width: 440, height: 40 }, 3000, () =>
      expect(isVisible(document.getElementById("f")!)).toBe(true),
    );
  });

  it("a field pushed off the top of the document is still treated as a honeypot", () => {
    document.body.innerHTML = `<input id="h" type="text">`;
    withLayout({ x: 10, y: -9999, top: -9999, bottom: -9979, left: 10, right: 200, width: 190, height: 20 }, 0, () =>
      expect(isVisible(document.getElementById("h")!)).toBe(false),
    );
  });
});
