// Regression tests for the system review (2026-09-30).
import { describe, expect, it } from "vitest";
import type { BrowserAction } from "@form-agent/contracts";
import { executeAction } from "../../src/actions/execute";
import { detectDialogs } from "../../src/perception/dialogs";
import { discoverFields } from "../../src/perception/fields";
import { isVisible } from "../../src/perception/visibility";

function act(kind: BrowserAction["kind"], target: Partial<BrowserAction["target"]>, value: string | null = null, checked: boolean | null = null): BrowserAction {
  return {
    action_id: "a", run_id: "local-test", tab_id: 1, origin: "http://localhost:3000", sequence_number: 1,
    source_observation_seq: 1, kind,
    target: { field_id: "x", role: "button", accessible_name: null, input_type: null, label: null, name_attr: null,
      autocomplete: null, placeholder: null, bounding_box: null, ...target } as BrowserAction["target"],
    resolved_value: value, value_ref: null, upload_file: null,
    expected_effect: { field_value: value, checked, selected_option: null, validation_error: null, expected_url_prefix: null, dialog_dismissed: null },
    risk: "low", idempotency_key: "k", method_hint: null, attempt: 0,
  } as unknown as BrowserAction;
}

describe("submission lock by effect", () => {
  it("a non-SUBMIT click that lands on a submit button is refused", async () => {
    let submitted = false;
    document.body.innerHTML = `<form><input id="n" name="n"><button id="go" type="submit">Submit application</button></form>`;
    document.querySelector("form")!.addEventListener("submit", (e) => { submitted = true; e.preventDefault(); });
    const r = await executeAction(document, act("CLICK", { field_id: "go", accessible_name: "Submit application" }));
    expect(r.status).toBe("FAILED");
    expect(submitted).toBe(false);
  });

  it("a wizard's Next (type=submit, named Next) is still allowed", async () => {
    let clicked = false;
    document.body.innerHTML = `<form><input type="submit" id="next" value="Next >"></form>`;
    document.getElementById("next")!.addEventListener("click", (e) => { clicked = true; e.preventDefault(); });
    const r = await executeAction(document, act("NAVIGATE_NEXT", { field_id: "next", accessible_name: "Next >" }));
    expect(r.status).toBe("EXECUTED");
    expect(clicked).toBe(true);
  });

  it("SET_CHECKBOX never clicks a submit input that happens to resolve", async () => {
    document.body.innerHTML = `<form><input type="submit" id="s" value="Pay now"></form>`;
    const r = await executeAction(document, act("SET_CHECKBOX", { field_id: "s", role: "checkbox" }, null, true));
    expect(r.status).toBe("FAILED");
  });
});

describe("dialogs are dismissed neutrally", () => {
  it("a confirm modal offers no dismiss target; a cookie banner may be rejected", () => {
    document.body.innerHTML = `
      <div role="dialog" id="confirm"><p>Are you sure you want to submit?</p><button>OK</button><button>Yes, submit</button></div>
      <div id="cookie-banner"><p>We use cookies</p><button>Reject all</button><button>Accept all</button></div>`;
    const by = Object.fromEntries(detectDialogs(document).map((d) => [d.dialog_id, d]));
    expect(by.confirm!.dismiss_target).toBeNull();
    expect(by["cookie-banner"]!.dismiss_target?.accessible_name).toBe("Reject all");
  });

  it("a modal with a Close button is dismissed with Close, never OK", () => {
    document.body.innerHTML = `<div role="dialog" id="m"><p>Notice</p><button>OK</button><button aria-label="Close">×</button></div>`;
    const d = detectDialogs(document)[0]!;
    expect(d.dismiss_target?.accessible_name).toBe("Close");
  });
});

describe("secrets stay on the page", () => {
  it("a password box inside a same-origin iframe is redacted", () => {
    document.body.innerHTML = `<iframe id="f"></iframe>`;
    const frame = (document.getElementById("f") as HTMLIFrameElement).contentDocument!;
    frame.body.innerHTML = `<label for="p">Login</label><input id="p" type="password" value="hunter2">`;
    const fields = discoverFields(document);
    const pw = fields.find((f) => f.field_id === "p");
    if (pw) {
      expect(pw.value_redacted).toBe(true);
      expect(pw.current_value).toBeNull();
      expect(pw.input_type).toBe("password");
    }
    expect(JSON.stringify(fields)).not.toContain("hunter2");
  });

  it("the executor refuses to type into a password or captcha box", async () => {
    document.body.innerHTML = `<label for="pw">Password</label><input id="pw" type="password">
      <label for="c">Captcha</label><input id="c" type="text">`;
    const r1 = await executeAction(document, act("SET_TEXT", { field_id: "pw", role: "textbox", input_type: "password" }, "hunter2"));
    const r2 = await executeAction(document, act("SET_TEXT", { field_id: "c", role: "textbox", input_type: "text" }, "x7k2"));
    expect(r1.status).toBe("FAILED");
    expect(r2.status).toBe("FAILED");
    expect((document.getElementById("pw") as HTMLInputElement).value).toBe("");
  });
});

describe("perception robustness", () => {
  it("a hidden honeypot inside a shadow root is not visible", () => {
    document.body.innerHTML = `<div id="host"></div>`;
    const root = document.getElementById("host")!.attachShadow({ mode: "open" });
    root.innerHTML = `<div style="visibility:hidden"><input id="trap" type="text"></div>`;
    expect(isVisible(root.getElementById("trap")!)).toBe(false);
  });

  it("id-less checkboxes sharing a name get distinct ids", () => {
    document.body.innerHTML = `
      <label><input type="checkbox" name="lang" value="ta"> Tamil</label>
      <label><input type="checkbox" name="lang" value="hi"> Hindi</label>`;
    const ids = discoverFields(document).map((f) => f.field_id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
