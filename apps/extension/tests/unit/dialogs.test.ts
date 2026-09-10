import { describe, expect, it } from "vitest";
import { detectDialogs } from "../../src/perception/dialogs";

describe("dialog detection with safe dismiss target", () => {
  it("detects a cookie banner and prefers a neutral/reject dismiss over accept", () => {
    document.body.innerHTML = `
      <div id="cookie-banner">
        We use cookies.
        <button id="reject">Reject all</button>
        <button id="accept">Accept all</button>
      </div>`;
    const dialogs = detectDialogs(document);
    expect(dialogs).toHaveLength(1);
    expect(dialogs[0]!.kind).toBe("cookie_banner");
    // Reject is preferred over Accept (conservative dismissal).
    expect(dialogs[0]!.dismiss_target!.field_id).toBe("reject");
  });

  it("dismiss target is always inside the dialog, never the page form", () => {
    document.body.innerHTML = `
      <form><button id="form-submit">Submit</button></form>
      <div class="consent-banner">Consent needed <button id="ok">Got it</button></div>`;
    const dialog = detectDialogs(document)[0]!;
    expect(dialog.dismiss_target!.field_id).toBe("ok");
    expect(dialog.dismiss_target!.field_id).not.toBe("form-submit");
  });

  it("classifies a role=dialog modal without cookie text as modal", () => {
    document.body.innerHTML = `
      <div role="dialog">Newsletter <button>Close</button></div>`;
    expect(detectDialogs(document)[0]!.kind).toBe("modal");
  });

  it("returns no dismiss target when the dialog has no button", () => {
    document.body.innerHTML = `<div role="dialog">Loading…</div>`;
    expect(detectDialogs(document)[0]!.dismiss_target).toBeNull();
  });
});

describe("a modal that holds the form", () => {
  it("is flagged contains_form so the driver does not dismiss it", async () => {
    const { detectDialogs } = await import("../../src/perception/dialogs");
    document.body.innerHTML = `
      <div role="dialog" id="register">
        <h2>Register Youth</h2>
        <label for="m">Mobile Number</label><input id="m" type="number">
        <button>Close</button><button>SUBMIT</button>
      </div>
      <div role="dialog" id="notice"><p>Site notice</p><button>Close</button></div>`;
    const dialogs = detectDialogs(document);
    expect(dialogs.find((d) => d.dialog_id === "register")?.contains_form).toBe(true);
    expect(dialogs.find((d) => d.dialog_id === "notice")?.contains_form).toBe(false);
  });
});

describe("aria-describedby is not an error", () => {
  it("only counts as a validation message when the control is aria-invalid", async () => {
    const { discoverFields } = await import("../../src/perception/fields");
    document.body.innerHTML = `
      <label for="s">State</label>
      <input id="s" type="text" aria-describedby="s-ph"><span id="s-ph">Select States</span>
      <label for="t">Town</label>
      <input id="t" type="text" aria-invalid="true" aria-describedby="t-err"><span id="t-err">Required</span>`;
    const fields = discoverFields(document);
    expect(fields.find((f) => f.field_id === "s")?.validation_message).toBeNull();
    expect(fields.find((f) => f.field_id === "t")?.validation_message).toBe("Required");
  });
});
