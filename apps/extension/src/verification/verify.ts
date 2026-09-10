// Independent deterministic verification (Module 4, invariant 7): compares
// the action's expected effect against a FRESH observation — never against
// the executor's own report.
import type {
  BrowserAction,
  FormField,
  PageObservation,
  VerificationResult,
} from "@form-agent/contracts";

// A select/combobox stores the option CODE as its value, but the expected
// value may be the human label (an optimistic cascading-dropdown assignment
// made before the options loaded, e.g. "Thane" vs code "476"). Treat the field
// as satisfied when the selected option's label matches, in either direction.
function optionValueSatisfied(field: FormField, expected: string): boolean {
  // A masked identifier never reports its value (it stays on the page); the
  // strongest check available is that it holds a value of the expected length.
  if (field.value_redacted) return expected.length > 0 && field.value_length === expected.length;
  const cur = field.current_value;
  if (cur === expected) return true;
  if (cur === null) return false;
  const norm = (s: string): string => s.trim().toLowerCase();
  if (norm(cur) === norm(expected)) return true;
  const opts = field.options ?? [];
  const labels = field.option_labels ?? [];
  const idx = opts.indexOf(cur);
  if (idx >= 0) {
    const label = labels[idx];
    if (label !== undefined && norm(label) === norm(expected)) return true;
  }
  return false;
}

// Classify WHY a value did not take, so recovery can pick a matched strategy
// rather than a generic retry. `expected` is known unsatisfied here.
function classifyValueFailure(
  field: FormField,
  expected: string,
): NonNullable<VerificationResult["failure_class"]> {
  const norm = (s: string): string => s.trim().toLowerCase();
  const cur = field.current_value;
  const empty = cur === null || cur === "";
  const options = field.options;
  if (options !== null && options !== undefined) {
    // Enumerated control (select/combobox).
    if (options.length === 0) return "cascade_pending"; // options not loaded yet
    const labels = field.option_labels ?? [];
    const matchable = options.some(
      (o, i) => norm(o) === norm(expected) || (labels[i] !== undefined && norm(labels[i]!) === norm(expected)),
    );
    if (!matchable) return "option_not_found"; // the option isn't offered
    return empty ? "value_not_applied" : "value_mismatch"; // offered but didn't stick / wrong one
  }
  // Free-text control: empty means the input never took; otherwise a different
  // value is present.
  return empty ? "value_not_applied" : "value_mismatch";
}

function findField(obs: PageObservation, action: BrowserAction): FormField | undefined {
  const target = action.target;
  if (!target) return undefined;
  return (obs.fields ?? []).find(
    (f) =>
      f.field_id === target.field_id ||
      (target.name_attr !== null && f.target.name_attr === target.name_attr),
  );
}

function verdict(
  action: BrowserAction,
  status: VerificationResult["status"],
  failureClass: VerificationResult["failure_class"],
  transition: VerificationResult["recommended_transition"],
  evidence: Partial<VerificationResult["evidence"]>,
): VerificationResult {
  return {
    action_id: action.action_id,
    status,
    evidence: {
      observed_value: null,
      field_valid: null,
      validation_message: null,
      page_fingerprint: null,
      notes: null,
      ...evidence,
    },
    failure_class: failureClass,
    recommended_transition: transition,
  };
}

export function verifyAction(
  action: BrowserAction,
  observation: PageObservation,
): VerificationResult {
  // Blocking page states take precedence over field-level checks. A login /
  // MFA gate blocks everything; a captcha is the human's to solve, so it only
  // blocks page transitions (submit / next) — other fields can still be filled.
  const pageTransition = action.kind === "SUBMIT" || action.kind === "NAVIGATE_NEXT";
  if (observation.login_detected || (observation.captcha_detected && pageTransition)) {
    return verdict(action, "NEEDS_USER", "login_required", "ASK_USER", {
      notes: observation.login_detected ? "login present" : "captcha present",
      page_fingerprint: observation.page_fingerprint,
    });
  }

  const expected = action.expected_effect;
  if (!expected) {
    // Non-mutating actions: nothing to compare beyond having a fresh page.
    return verdict(action, "SUCCESS", null, "CONTINUE", {
      page_fingerprint: observation.page_fingerprint,
    });
  }

  if (expected.expected_url_prefix && !observation.url.startsWith(expected.expected_url_prefix)) {
    return verdict(action, "RETRYABLE_FAILURE", "navigation_failed", "REPERCEIVE", {
      notes: `url ${observation.url} lacks prefix ${expected.expected_url_prefix}`,
      page_fingerprint: observation.page_fingerprint,
    });
  }

  if (expected.dialog_dismissed) {
    const stillOpen = (observation.dialogs ?? []).length > 0;
    if (stillOpen) {
      return verdict(action, "RETRYABLE_FAILURE", "value_mismatch", "RETRY", {
        notes: "dialog still present",
        page_fingerprint: observation.page_fingerprint,
      });
    }
  }

  const wantsFieldCheck =
    expected.field_value !== null ||
    expected.checked !== null ||
    expected.selected_option !== null ||
    expected.validation_error !== null;

  if (wantsFieldCheck) {
    const field = findField(observation, action);
    if (!field) {
      return verdict(action, "NEEDS_REPERCEPTION", "element_not_found", "REPERCEIVE", {
        notes: `field ${action.target?.field_id} absent from fresh observation`,
        page_fingerprint: observation.page_fingerprint,
      });
    }

    const evidence = {
      observed_value: field.current_value,
      field_valid: field.validation_message === null,
      validation_message: field.validation_message,
      page_fingerprint: observation.page_fingerprint,
    };

    const expectedValue = expected.field_value ?? expected.selected_option;
    if (expectedValue !== null && expectedValue !== undefined) {
      if (!optionValueSatisfied(field, expectedValue)) {
        const cls = classifyValueFailure(field, expectedValue);
        return verdict(action, "RETRYABLE_FAILURE", cls, "RETRY", evidence);
      }
    }
    if (expected.checked !== null && expected.checked !== undefined) {
      if (field.checked !== expected.checked) {
        return verdict(action, "RETRYABLE_FAILURE", "value_mismatch", "RETRY", evidence);
      }
    }
    if (expected.validation_error === false && field.validation_message !== null) {
      return verdict(action, "RETRYABLE_FAILURE", "validation_error", "RETRY", evidence);
    }
    return verdict(action, "SUCCESS", null, "CONTINUE", evidence);
  }

  return verdict(action, "SUCCESS", null, "CONTINUE", {
    page_fingerprint: observation.page_fingerprint,
  });
}
