// Executor unit tests: framework-compatible events, per-kind behavior,
// classified failures (Module 3).
import { describe, expect, it } from "vitest";
import type { BrowserAction } from "@form-agent/contracts";
import { executeAction } from "../../src/actions/execute";

function action(overrides: Partial<BrowserAction>): BrowserAction {
  return {
    action_id: "a-1", run_id: "run-1", tab_id: 1, origin: "http://localhost:3000",
    sequence_number: 1, kind: "SET_TEXT",
    target: {
      field_id: "f", role: "textbox", accessible_name: null, input_type: "text",
      label: null, name_attr: null, autocomplete: null, placeholder: null, bounding_box: null,
    },
    value_ref: null, resolved_value: null,
    expected_effect: {
      field_value: null, checked: null, selected_option: null, validation_error: null,
      dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
    },
    risk: "low", idempotency_key: "k-1", source_observation_seq: null, approval_token_id: null, upload_file: null,
    ...overrides,
  };
}

describe("SET_TEXT", () => {
  it("sets value via native setter and fires bubbling input/change (framework compat)", () => {
    document.body.innerHTML = `<form id="f-form"><input id="f" type="text"></form>`;
    const events: string[] = [];
    // Listen on the FORM to prove events bubble (React-style delegation).
    document.getElementById("f-form")!.addEventListener("input", (e) => {
      events.push(`input:${(e.target as HTMLInputElement).value}`);
    });
    document.getElementById("f-form")!.addEventListener("change", () => events.push("change"));

    const result = executeAction(document, action({ resolved_value: "Ada Lovelace" }));
    expect(result.status).toBe("EXECUTED");
    expect((document.getElementById("f") as HTMLInputElement).value).toBe("Ada Lovelace");
    expect(events).toEqual(["input:Ada Lovelace", "change"]);
  });

  it("fails cleanly on a disabled field", () => {
    document.body.innerHTML = `<input id="f" type="text" disabled>`;
    const result = executeAction(document, action({ resolved_value: "x" }));
    expect(result.status).toBe("FAILED");
    expect(result.error).toContain("disabled");
  });

  it("fails cleanly when the element does not exist", () => {
    document.body.innerHTML = ``;
    const result = executeAction(document, action({ resolved_value: "x" }));
    expect(result.status).toBe("FAILED");
    expect(result.error).toContain("no element");
  });
});

describe("SELECT_OPTION", () => {
  it("selects an existing option", () => {
    document.body.innerHTML = `<select id="f"><option value="">--</option><option value="IN">India</option></select>`;
    const result = executeAction(
      document,
      action({ kind: "SELECT_OPTION", resolved_value: "IN" }),
    );
    expect(result.status).toBe("EXECUTED");
    expect((document.getElementById("f") as HTMLSelectElement).value).toBe("IN");
  });

  it("fails on a missing option instead of guessing", () => {
    document.body.innerHTML = `<select id="f"><option value="IN">India</option></select>`;
    const result = executeAction(
      document,
      action({ kind: "SELECT_OPTION", resolved_value: "XX" }),
    );
    expect(result.status).toBe("FAILED");
    expect(result.error).toContain("not present");
  });
});

describe("SET_CHECKBOX / SET_RADIO", () => {
  it("checks a checkbox only when state differs (idempotent at DOM level)", () => {
    document.body.innerHTML = `<input id="f" type="checkbox">`;
    const el = document.getElementById("f") as HTMLInputElement;
    let clicks = 0;
    el.addEventListener("click", () => clicks++);
    const a = action({
      kind: "SET_CHECKBOX",
      expected_effect: {
        field_value: null, checked: true, selected_option: null, validation_error: null,
        dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
      },
    });
    expect(executeAction(document, a).status).toBe("EXECUTED");
    expect(el.checked).toBe(true);
    expect(executeAction(document, a).status).toBe("EXECUTED"); // already checked
    expect(clicks).toBe(1);
  });

  it("selects the radio matching the resolved value via the group target", () => {
    document.body.innerHTML = `
      <input type="radio" name="contact" value="email">
      <input type="radio" name="contact" value="phone">`;
    const result = executeAction(
      document,
      action({
        kind: "SET_RADIO",
        resolved_value: "phone",
        target: {
          field_id: "radio-group:contact", role: "radiogroup", accessible_name: null,
          input_type: "radio", label: null, name_attr: "contact", autocomplete: null,
          placeholder: null, bounding_box: null,
        },
      }),
    );
    expect(result.status).toBe("EXECUTED");
    const phone = document.querySelector<HTMLInputElement>('input[value="phone"]')!;
    expect(phone.checked).toBe(true);
  });
});

describe("UPLOAD_FILE", () => {
  const uploadRef = { filename: "resume.txt", mime_type: "text/plain",
    content_base64: btoa("hello resume"), document_id: null };

  // The DataTransfer attach path is native to Chrome (jsdom lacks it) and is
  // proven by the real-browser E2E; unit tests cover the guard paths.
  it("fails when the target is not a file input", () => {
    document.body.innerHTML = `<input id="f" type="text">`;
    const result = executeAction(document, action({ kind: "UPLOAD_FILE", upload_file: uploadRef }));
    expect(result.status).toBe("FAILED");
    expect(result.error).toContain("not a file input");
  });
});
