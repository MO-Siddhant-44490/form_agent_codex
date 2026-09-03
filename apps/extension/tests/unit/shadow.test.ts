import { describe, expect, it } from "vitest";
import { deepGetById, deepQueryAll } from "../../src/perception/shadow";
import { discoverFields } from "../../src/perception/fields";

describe("deep traversal across open shadow roots", () => {
  it("finds inputs inside an open shadow root", () => {
    document.body.innerHTML = `<div id="host"></div>`;
    const host = document.getElementById("host")!;
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `<label for="s-name">Name</label><input id="s-name" type="text">`;

    const inputs = deepQueryAll<HTMLInputElement>(document, "input");
    expect(inputs).toHaveLength(1);
    expect(inputs[0]!.id).toBe("s-name");
    expect(deepGetById(document, "s-name")).toBe(inputs[0]);
  });

  it("does NOT descend into closed shadow roots", () => {
    document.body.innerHTML = `<div id="host"></div>`;
    const shadow = document.getElementById("host")!.attachShadow({ mode: "closed" });
    shadow.innerHTML = `<input id="hidden-in-closed" type="text">`;
    expect(deepQueryAll(document, "input")).toHaveLength(0);
  });

  it("discoverFields resolves labels inside a shadow root", () => {
    document.body.innerHTML = `<div id="host"></div>`;
    const shadow = document.getElementById("host")!.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <label for="email">Email address</label>
      <input id="email" name="email" type="email" required>`;
    const fields = discoverFields(document);
    expect(fields).toHaveLength(1);
    expect(fields[0]).toMatchObject({ label: "Email address", input_type: "email", required: true });
  });

  it("discovers fields across a mix of light DOM and shadow DOM", () => {
    document.body.innerHTML = `
      <input id="light" type="text">
      <div id="host"></div>`;
    const shadow = document.getElementById("host")!.attachShadow({ mode: "open" });
    shadow.innerHTML = `<input id="shadowed" type="email">`;
    const ids = discoverFields(document).map((f) => f.field_id);
    expect(ids).toEqual(["light", "shadowed"]);
  });
});
