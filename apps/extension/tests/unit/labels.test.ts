import { describe, expect, it } from "vitest";
import { accessibleName, computedRole, explicitLabel } from "../../src/perception/labels";

function mount(html: string): void {
  document.body.innerHTML = html;
}

describe("label resolution order (plan.md §9)", () => {
  it("prefers explicit label[for]", () => {
    mount(`<label for="a">Full name</label><input id="a" placeholder="name here">`);
    const el = document.getElementById("a")!;
    expect(explicitLabel(el)).toBe("Full name");
    expect(accessibleName(el)).toBe("Full name");
  });

  it("falls back to wrapping label", () => {
    mount(`<label>Email <input id="a"></label>`);
    expect(explicitLabel(document.getElementById("a")!)).toBe("Email");
  });

  it("aria-label beats html label", () => {
    mount(`<label for="a">Visual</label><input id="a" aria-label="Accessible">`);
    expect(accessibleName(document.getElementById("a")!)).toBe("Accessible");
  });

  it("aria-labelledby joins referenced texts", () => {
    mount(`<span id="s1">Date</span><span id="s2">of birth</span><input id="a" aria-labelledby="s1 s2">`);
    expect(accessibleName(document.getElementById("a")!)).toBe("Date of birth");
  });

  it("placeholder is a last resort", () => {
    mount(`<input id="a" placeholder="Phone">`);
    expect(accessibleName(document.getElementById("a")!)).toBe("Phone");
  });
});

describe("computed role", () => {
  it.each([
    ["text", "textbox"],
    ["email", "textbox"],
    ["number", "spinbutton"],
    ["checkbox", "checkbox"],
    ["radio", "radio"],
    ["search", "searchbox"],
  ])("input[type=%s] -> %s", (type, role) => {
    mount(`<input id="a" type="${type}">`);
    expect(computedRole(document.getElementById("a")!)).toBe(role);
  });

  it("select -> combobox, textarea -> textbox, explicit role wins", () => {
    mount(`<select id="s"></select><textarea id="t"></textarea><input id="c" role="combobox">`);
    expect(computedRole(document.getElementById("s")!)).toBe("combobox");
    expect(computedRole(document.getElementById("t")!)).toBe("textbox");
    expect(computedRole(document.getElementById("c")!)).toBe("combobox");
  });
});
