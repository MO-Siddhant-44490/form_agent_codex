import { describe, expect, it } from "vitest";
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
