import { describe, expect, it } from "vitest";
import { discoverFields } from "../../src/perception/fields";

describe("field discovery", () => {
  it("discovers standard controls with labels, skips buttons and hidden", () => {
    document.body.innerHTML = `
      <form>
        <label for="name">Name</label><input id="name" type="text" required>
        <label for="country">Country</label>
        <select id="country"><option value="">--</option><option value="IN">India</option></select>
        <input type="hidden" name="csrf" value="tok">
        <input type="submit" value="Go">
      </form>`;
    const fields = discoverFields(document);
    expect(fields.map((f) => f.field_id)).toEqual(["name", "country"]);
    expect(fields[0]).toMatchObject({ label: "Name", required: true, input_type: "text" });
    expect(fields[1]!.options).toEqual(["", "IN"]);
  });

  it("collapses radio groups into one radiogroup field", () => {
    document.body.innerHTML = `
      <fieldset><legend>Contact method</legend>
        <input type="radio" id="r1" name="contact" value="email" checked>
        <input type="radio" id="r2" name="contact" value="phone">
      </fieldset>`;
    const fields = discoverFields(document);
    expect(fields).toHaveLength(1);
    expect(fields[0]).toMatchObject({
      field_id: "radio-group:contact",
      input_type: "radio",
      options: ["email", "phone"],
      current_value: "email",
      checked: true,
      label: "Contact method",
    });
    expect(fields[0]!.target.role).toBe("radiogroup");
  });

  it("never captures credential values (invariant 2)", () => {
    document.body.innerHTML = `
      <label for="pw">Password</label>
      <input id="pw" type="password" value="hunter2">
      <label for="otp">Code</label>
      <input id="otp" type="text" autocomplete="one-time-code" value="123456">`;
    const fields = discoverFields(document);
    expect(fields).toHaveLength(2);
    for (const field of fields) {
      expect(field.value_redacted).toBe(true);
      expect(field.current_value).toBeNull();
    }
    expect(JSON.stringify(fields)).not.toContain("hunter2");
    expect(JSON.stringify(fields)).not.toContain("123456");
  });

  it("excludes offscreen honeypots entirely", () => {
    document.body.innerHTML = `
      <input id="real" type="text">
      <div style="position:absolute;left:-9999px"><input id="website" name="website"></div>`;
    const ids = discoverFields(document).map((f) => f.field_id);
    expect(ids).toEqual(["real"]);
  });

  it("captures non-sensitive current values and checkbox state", () => {
    document.body.innerHTML = `
      <input id="city" type="text" value="Pune">
      <input id="sub" type="checkbox" checked>`;
    const fields = discoverFields(document);
    expect(fields[0]).toMatchObject({ current_value: "Pune", value_redacted: false });
    expect(fields[1]).toMatchObject({ checked: true, current_value: null });
  });
});
