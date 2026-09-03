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
