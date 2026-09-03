import { describe, expect, it } from "vitest";
import { detectNavigation, detectStepLabel } from "../../src/perception/navigation";

describe("navigation control detection (multi-page)", () => {
  it("classifies next / previous / submit by accessible name", () => {
    document.body.innerHTML = `
      <button id="back">Back</button>
      <button id="next">Continue</button>
      <button id="go">Submit application</button>`;
    const nav = detectNavigation(document);
    const byId = Object.fromEntries(nav.map((n) => [n.control_id, n.kind]));
    expect(byId.back).toBe("previous");
    expect(byId.next).toBe("next");
    expect(byId.go).toBe("submit");
  });

  it("treats 'save & continue' as next, not submit", () => {
    document.body.innerHTML = `<button id="sc">Save & Continue</button>`;
    expect(detectNavigation(document)[0]!.kind).toBe("next");
  });

  it("input[type=submit] with generic value is submit", () => {
    document.body.innerHTML = `<input id="s" type="submit" value="Go">`;
    expect(detectNavigation(document)[0]!.kind).toBe("submit");
  });

  it("ignores hidden navigation controls", () => {
    document.body.innerHTML = `<button id="n" style="display:none">Next</button>`;
    expect(detectNavigation(document)).toHaveLength(0);
  });

  it("reads a step progress label", () => {
    document.body.innerHTML = `<p>Step 2 of 4</p><button>Next</button>`;
    expect(detectStepLabel(document)).toBe("Step 2 of 4");
  });

  it("no step label returns null", () => {
    document.body.innerHTML = `<button>Next</button>`;
    expect(detectStepLabel(document)).toBeNull();
  });
});
