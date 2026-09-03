// Independent deterministic verification (Module 4, invariant 7): compares
// the action's expected effect against a FRESH observation — never against
// the executor's own report.
import type {
  BrowserAction,
  FormField,
  PageObservation,
  VerificationResult,
} from "@form-agent/contracts";

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
  // Blocking page states take precedence over field-level checks.
  if (observation.login_detected || observation.captcha_detected) {
    return verdict(action, "NEEDS_USER", "login_required", "ASK_USER", {
      notes: observation.captcha_detected ? "captcha present" : "login present",
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
      if (field.current_value !== expectedValue) {
        return verdict(action, "RETRYABLE_FAILURE", "value_mismatch", "RETRY", evidence);
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
