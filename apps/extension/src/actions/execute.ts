// Deterministic executor (Module 3): applies exactly one validated typed
// action to the live DOM. No model output executes directly (invariant 5);
// every mutation goes through framework-compatible native events.
import type { ActionResult, BrowserAction } from "@form-agent/contracts";
import { isFormControl, isInputEl, isSelectEl } from "./dom-types";
import { fireInputEvents, focusThen, setNativeValue } from "./events";
import { resolveTarget, type Resolved } from "./resolve";

function result(
  action: BrowserAction,
  status: ActionResult["status"],
  extra: Partial<ActionResult> = {},
): ActionResult {
  return {
    action_id: action.action_id,
    status,
    rejection_reason: null,
    error: null,
    executed_at: new Date().toISOString(),
    ...extra,
  };
}

function failed(action: BrowserAction, error: string): ActionResult {
  return result(action, "FAILED", { error });
}

const TEXTUAL_KINDS = new Set(["SET_TEXT", "SET_NUMBER", "SET_DATE", "SELECT_OPTION"]);

export function executeAction(doc: Document, action: BrowserAction): ActionResult {
  switch (action.kind) {
    case "SET_TEXT":
    case "SET_NUMBER":
    case "SET_DATE":
    case "SELECT_OPTION":
    case "SET_CHECKBOX":
    case "SET_RADIO":
    case "CLICK":
    case "DISMISS_DIALOG":
    case "SUBMIT":
    case "NAVIGATE_NEXT":
      return executeTargeted(doc, action);
    case "SCROLL": {
      doc.defaultView?.scrollBy({ top: doc.defaultView.innerHeight * 0.8 });
      return result(action, "EXECUTED");
    }
    case "WAIT_FOR_STABLE_PAGE":
      // The stability wait happens in the content-script command loop before
      // this returns; by the time we are here the DOM was quiet.
      return result(action, "EXECUTED");
    case "UPLOAD_FILE":
      // Not yet implemented (later Slice 5 chunk): reject loudly.
      return result(action, "REJECTED", {
        rejection_reason: "unsupported",
        error: `${action.kind} is not supported in this build`,
      });
  }
}

function executeTargeted(doc: Document, action: BrowserAction): ActionResult {
  if (!action.target) return failed(action, "action has no target");
  const resolved = resolveTarget(doc, action.target);
  if (resolved.kind === "not-found") return failed(action, resolved.detail);

  const value = action.resolved_value;

  if (TEXTUAL_KINDS.has(action.kind)) {
    if (resolved.kind !== "element") return failed(action, "textual action on radio group");
    const el = resolved.element;
    if (!isFormControl(el)) {
      return failed(action, "target is not a form control");
    }
    if (value === null || value === undefined) return failed(action, "no resolved value");
    if (el.disabled || (("readOnly" in el) && el.readOnly)) {
      return failed(action, "target is disabled or read-only");
    }
    if (isSelectEl(el) && !Array.from(el.options).some((o) => o.value === value)) {
      return failed(action, `option ${value} not present`);
    }
    focusThen(el, () => {
      setNativeValue(el, value);
      fireInputEvents(el);
    });
    return result(action, "EXECUTED");
  }

  if (action.kind === "SET_CHECKBOX") {
    if (resolved.kind !== "element" || !isInputEl(resolved.element)) {
      return failed(action, "target is not a checkbox");
    }
    const el = resolved.element;
    const desired = action.expected_effect?.checked;
    if (desired === null || desired === undefined) return failed(action, "no desired checked state");
    if (el.checked !== desired) el.click(); // real click fires input+change natively
    return result(action, "EXECUTED");
  }

  if (action.kind === "SET_RADIO") {
    if (value === null || value === undefined) return failed(action, "no resolved value");
    const radios =
      resolved.kind === "radio-group"
        ? resolved.radios
        : isInputEl(resolved.element)
          ? [resolved.element]
          : [];
    const match = radios.find((r) => r.value === value);
    if (!match) return failed(action, `no radio with value ${value}`);
    if (!match.checked) match.click();
    return result(action, "EXECUTED");
  }

  // CLICK / DISMISS_DIALOG / SUBMIT: a real click on the resolved element.
  if (resolved.kind !== "element") return failed(action, "click needs a single element");
  (resolved.element as HTMLElement).click();
  return result(action, "EXECUTED");
}
