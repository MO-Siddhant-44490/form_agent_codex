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


describe("radio group option labels", () => {
  it("captures each radio's label parallel to its value so labels can match codes", () => {
    document.body.innerHTML = `
      <fieldset><legend>Gender</legend>
        <input type="radio" name="Sex" id="m" value="M"><label for="m">Male</label>
        <input type="radio" name="Sex" id="f" value="F"><label for="f">Female</label>
      </fieldset>`;
    const group = discoverFields(document).find((f) => f.field_id === "radio-group:Sex")!;
    expect(group.options).toEqual(["M", "F"]);
    expect(group.option_labels).toEqual(["Male", "Female"]);
  });
});


describe("adjacent-text labels for unlabelled radios and checkboxes", () => {
  it("reads the loose text next to each radio (the pgportal pattern)", () => {
    document.body.innerHTML = `
      <div class="q">Gender
        <input type="radio" name="Sex" id="Sex_M" value="M"> Male
        <input type="radio" name="Sex" id="Sex_F" value="F"> Female
        <input type="radio" name="Sex" id="Sex_O" value="O"> Transgender
      </div>
      <p><input type="checkbox" id="nl"> Send me the newsletter</p>`;
    const fields = discoverFields(document);
    expect(fields.find((f) => f.field_id === "radio-group:Sex")!.option_labels).toEqual(["Male", "Female", "Transgender"]);
    expect(fields.find((f) => f.field_id === "nl")!.label).toBe("Send me the newsletter");
  });
});


describe("constraints declared by validation libraries", () => {
  it("reads ASP.NET / Parsley / jQuery-Validate pattern and max-length attributes", () => {
    document.body.innerHTML = `
      <label for="m">Mobile number</label>
      <input id="m" type="text" data-val="true" data-val-regex-pattern="^[5|6|7|8|9]\\d{9}$"
             data-val-regex="Please enter 10 digit valid Mobile number">
      <label for="a">Address</label><input id="a" type="text" data-parsley-maxlength="60">
      <label for="z">PIN</label><input id="z" type="text" data-rule-pattern="/^\\d{6}$/">
      <label for="n">Name</label><input id="n" type="text" maxlength="40" pattern="[A-Za-z ]+">`;
    const by = Object.fromEntries(discoverFields(document).map((f) => [f.field_id, f]));
    expect(by.m!.pattern).toBe("^[5|6|7|8|9]\\d{9}$");
    expect(by.a!.max_length).toBe(60);
    expect(by.z!.pattern).toBe("^\\d{6}$");
    expect(by.n!.pattern).toBe("[A-Za-z ]+");
    expect(by.n!.max_length).toBe(40);
  });
});
