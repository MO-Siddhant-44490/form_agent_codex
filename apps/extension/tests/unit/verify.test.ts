// Verifier unit tests (Module 4): success, wrong value, validation error,
// missing field, login/captcha takeover. Every path returns a classified
// status — a false SUCCESS here is the highest-severity bug class.
import { describe, expect, it } from "vitest";
import type { BrowserAction, FormField, PageObservation } from "@form-agent/contracts";
import { verifyAction } from "../../src/verification/verify";

function field(overrides: Partial<FormField>): FormField {
  return {
    field_id: "dob",
    target: {
      field_id: "dob", role: "textbox", accessible_name: "Date of birth", input_type: "date",
      label: null, name_attr: "date_of_birth", autocomplete: null, placeholder: null, bounding_box: null,
    },
    input_type: "date", label: "Date of birth", accessible_name: "Date of birth",
    required: true, disabled: false, readonly: false, visible: true, checked: null,
    current_value: null, value_redacted: false, options: null, option_labels: null, validation_message: null,
    nearby_text: null,
    ...overrides,
  };
}

function observation(fields: FormField[], overrides: Partial<PageObservation> = {}): PageObservation {
  return {
    run_id: "run-1", tab_id: 1, frame_id: "main",
    url: "http://127.0.0.1:4173/basic-form/", origin: "http://127.0.0.1:4173",
    title: "t", page_fingerprint: "sha256:abc", observation_seq: 2,
    observed_at: new Date().toISOString(), classification: [{ label: "form", confidence: 0.9 }],
    fields, dialogs: [], iframes: [], navigation: [], step_label: null,
    login_detected: false, captcha_detected: false, dom_stable: true,
    ...overrides,
  };
}

function setDob(expectedValue: string): BrowserAction {
  return {
    action_id: "a-1", run_id: "run-1", tab_id: 1, origin: "http://127.0.0.1:4173",
    sequence_number: 1, kind: "SET_DATE",
    target: {
      field_id: "dob", role: "textbox", accessible_name: "Date of birth", input_type: "date",
      label: null, name_attr: "date_of_birth", autocomplete: null, placeholder: null, bounding_box: null,
    },
    value_ref: "fact://dob", resolved_value: expectedValue,
    expected_effect: {
      field_value: expectedValue, checked: null, selected_option: null, validation_error: false,
      dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
    },
    risk: "low", idempotency_key: "k", source_observation_seq: 1, approval_token_id: null, upload_file: null,
  };
}

describe("verifyAction", () => {
  it("SUCCESS when observed value matches and no validation error", () => {
    const v = verifyAction(setDob("1998-04-17"), observation([field({ current_value: "1998-04-17" })]));
    expect(v.status).toBe("SUCCESS");
    expect(v.recommended_transition).toBe("CONTINUE");
    expect(v.evidence.observed_value).toBe("1998-04-17");
  });

  it("never SUCCESS on a wrong observed value", () => {
    const v = verifyAction(setDob("1998-04-17"), observation([field({ current_value: "1998-04-18" })]));
    expect(v.status).toBe("RETRYABLE_FAILURE");
    expect(v.failure_class).toBe("value_mismatch");
  });

  it("never SUCCESS while a validation error remains", () => {
    const v = verifyAction(
      setDob("1998-04-17"),
      observation([field({ current_value: "1998-04-17", validation_message: "Date is in the future" })]),
    );
    expect(v.status).toBe("RETRYABLE_FAILURE");
    expect(v.failure_class).toBe("validation_error");
    expect(v.evidence.validation_message).toBe("Date is in the future");
  });

  it("requests re-perception when the field vanished", () => {
    const v = verifyAction(setDob("1998-04-17"), observation([]));
    expect(v.status).toBe("NEEDS_REPERCEPTION");
    expect(v.failure_class).toBe("element_not_found");
  });

  it("requests user takeover when login or captcha appears", () => {
    const v = verifyAction(
      setDob("1998-04-17"),
      observation([field({})], { login_detected: true, captcha_detected: true }),
    );
    expect(v.status).toBe("NEEDS_USER");
    expect(v.recommended_transition).toBe("ASK_USER");
  });

  it("checkbox expectation compares checked state", () => {
    const a: BrowserAction = {
      ...setDob("unused"),
      kind: "SET_CHECKBOX",
      resolved_value: null,
      expected_effect: {
        field_value: null, checked: true, selected_option: null, validation_error: null,
        dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
      },
    };
    const good = verifyAction(a, observation([field({ input_type: "checkbox", checked: true })]));
    expect(good.status).toBe("SUCCESS");
    const bad = verifyAction(a, observation([field({ input_type: "checkbox", checked: false })]));
    expect(bad.status).toBe("RETRYABLE_FAILURE");
  });

  it("url prefix expectation detects failed navigation", () => {
    const a: BrowserAction = {
      ...setDob("unused"),
      kind: "SUBMIT",
      resolved_value: null,
      approval_token_id: "tok-1", upload_file: null,
      expected_effect: {
        field_value: null, checked: null, selected_option: null, validation_error: null,
        dialog_dismissed: null, navigation_expected: true,
        expected_url_prefix: "http://127.0.0.1:4173/thank-you",
      },
    };
    const v = verifyAction(a, observation([field({})]));
    expect(v.status).toBe("RETRYABLE_FAILURE");
    expect(v.failure_class).toBe("navigation_failed");
  });
});
