import { describe, expect, it } from "vitest";
import { discoverFields } from "../../src/perception/fields";
import { detectCaptcha, detectLogin } from "../../src/perception/page";
import { classifyPurpose } from "../../src/perception/purpose";

describe("classifyPurpose", () => {
  it("a masked national identifier is standard data, still redacted", () => {
    expect(
      classifyPurpose({ inputType: "password", texts: ["Aadhaar Number/Virtual ID *", "aadhaar"] }),
    ).toBe("standard");
    expect(classifyPurpose({ inputType: "password", texts: ["PAN Number"] })).toBe("standard");
  });

  it("passwords, OTPs and PINs are credentials whatever the input type", () => {
    expect(classifyPurpose({ inputType: "password", texts: ["Enter Password"] })).toBe("credential");
    expect(classifyPurpose({ inputType: "password", texts: [] })).toBe("credential");
    expect(classifyPurpose({ inputType: "text", texts: ["Enter OTP"] })).toBe("credential");
    expect(classifyPurpose({ inputType: "number", texts: ["Mobile OTP", "otp"] })).toBe("credential");
    expect(classifyPurpose({ inputType: "password", texts: ["Aadhaar OTP"] })).toBe("credential");
    expect(classifyPurpose({ inputType: "text", autocomplete: "one-time-code", texts: ["Code"] })).toBe(
      "credential",
    );
  });

  it("a postal PIN code is not a secret", () => {
    expect(classifyPurpose({ inputType: "text", texts: ["PIN Code"] })).toBe("standard");
    expect(classifyPurpose({ inputType: "text", texts: ["Pincode", "pincode"] })).toBe("standard");
  });

  it("captcha boxes and consent controls", () => {
    expect(classifyPurpose({ inputType: "text", texts: ["Captcha *"] })).toBe("captcha");
    expect(classifyPurpose({ inputType: "text", texts: ["Enter the code shown above"] })).toBe("captcha");
    expect(
      classifyPurpose({
        inputType: "checkbox",
        texts: ["I consent to the use of my Aadhaar details for PM Internship Scheme."],
      }),
    ).toBe("consent");
    expect(classifyPurpose({ inputType: "checkbox", texts: ["I agree to the Terms & Conditions"] })).toBe(
      "consent",
    );
    expect(classifyPurpose({ inputType: "checkbox", texts: ["Subscribe to newsletter"] })).toBe("standard");
  });
});

describe("purpose in discovered fields", () => {
  it("a masked Aadhaar input is fillable data with a redacted value and a length", () => {
    document.body.innerHTML = `
      <label for="aad">Aadhaar Number/Virtual ID *</label>
      <input id="aad" type="password" value="976487322387">
      <label for="cap">Captcha *</label><input id="cap" type="text">
      <label><input id="c" type="checkbox"> I consent to the use of my Aadhaar details.</label>`;
    const fields = discoverFields(document);
    const aad = fields.find((f) => f.field_id === "aad")!;
    expect(aad.purpose).toBe("standard");
    expect(aad.value_redacted).toBe(true);
    expect(aad.current_value).toBeNull();
    expect(aad.value_length).toBe(12);
    expect(fields.find((f) => f.field_id === "cap")!.purpose).toBe("captcha");
    expect(fields.find((f) => f.field_id === "c")!.purpose).toBe("consent");
    // Not a login page; a captcha is present.
    expect(detectLogin(document, fields)).toBe(false);
    expect(detectCaptcha(document, fields)).toBe(true);
  });

  it("a real password box is a credential and marks a login gate", () => {
    document.body.innerHTML = `<label for="pw">Password</label><input id="pw" type="password" value="x">`;
    const fields = discoverFields(document);
    expect(fields[0]!.purpose).toBe("credential");
    expect(fields[0]!.value_length).toBeNull();
    expect(detectLogin(document, fields)).toBe(true);
  });
});
