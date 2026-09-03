// DOM/accessibility field discovery (Module 2). Pure functions over a
// Document; no page mutation, no model calls, no value capture for
// credential-like inputs (invariant 2).
import type { FormField, TargetDescriptor } from "@form-agent/contracts";
import { accessibleName, computedRole, explicitLabel, groupLegend } from "./labels";
import { hasLayout, isVisible } from "./visibility";

type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

// Values of these inputs must never leave the page (invariant 2, threat T6).
const CREDENTIAL_INPUT_TYPES = new Set(["password"]);
const CREDENTIAL_AUTOCOMPLETE = new Set([
  "current-password",
  "new-password",
  "one-time-code",
  "cc-number",
  "cc-csc",
]);

export function isCredentialControl(el: Control): boolean {
  if (el instanceof HTMLInputElement && CREDENTIAL_INPUT_TYPES.has(el.type)) return true;
  const autocomplete = el.getAttribute("autocomplete")?.trim().toLowerCase();
  return autocomplete !== undefined && CREDENTIAL_AUTOCOMPLETE.has(autocomplete);
}

function inputType(el: Control): string {
  if (el instanceof HTMLInputElement) return el.type;
  if (el instanceof HTMLSelectElement) return el.type; // select-one | select-multiple
  return "textarea";
}

function boundingBox(el: HTMLElement): [number, number, number, number] | null {
  if (!hasLayout(el.ownerDocument)) return null;
  const r = el.getBoundingClientRect();
  return [r.x, r.y, r.width, r.height];
}

function fieldIdFor(el: Control, index: number): string {
  if (el.id) return el.id;
  if (el.name) return `${el.name}:${inputType(el)}`;
  return `field-${index}:${inputType(el)}`;
}

function descriptor(el: Control, fieldId: string): TargetDescriptor {
  return {
    field_id: fieldId,
    role: computedRole(el),
    accessible_name: accessibleName(el),
    input_type: inputType(el),
    label: explicitLabel(el),
    name_attr: el.name || null,
    autocomplete: el.getAttribute("autocomplete") ?? null,
    placeholder: el.getAttribute("placeholder") ?? null,
    bounding_box: boundingBox(el),
  };
}

function validationMessage(el: Control): string | null {
  // Deterministic local validity: only report once the user/agent has put a
  // value in (empty required fields are "not yet filled", not "invalid").
  if (el.value === "" || el.checkValidity()) return null;
  return el.validationMessage || null;
}

function baseField(el: Control, fieldId: string): FormField {
  const credential = isCredentialControl(el);
  return {
    field_id: fieldId,
    target: descriptor(el, fieldId),
    input_type: inputType(el),
    label: explicitLabel(el),
    accessible_name: accessibleName(el),
    required: el.required,
    disabled: el.disabled,
    readonly: "readOnly" in el ? el.readOnly : false,
    visible: true,
    checked: el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")
      ? el.checked
      : null,
    current_value: credential ? null : currentValue(el),
    value_redacted: credential,
    options: el instanceof HTMLSelectElement
      ? Array.from(el.options).map((o) => o.value)
      : null,
    validation_message: credential ? null : validationMessage(el),
    nearby_text: groupLegend(el),
  };
}

function currentValue(el: Control): string | null {
  if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
    return null; // represented by `checked`
  }
  return el.value === "" ? null : el.value;
}

function radioGroupField(radios: HTMLInputElement[], name: string): FormField {
  const first = radios[0]!;
  const fieldId = `radio-group:${name}`;
  const checked = radios.find((r) => r.checked);
  return {
    field_id: fieldId,
    target: { ...descriptor(first, fieldId), role: "radiogroup" },
    input_type: "radio",
    label: groupLegend(first) ?? explicitLabel(first),
    accessible_name: groupLegend(first) ?? accessibleName(first),
    required: radios.some((r) => r.required),
    disabled: radios.every((r) => r.disabled),
    readonly: false,
    visible: true,
    checked: checked !== undefined,
    current_value: checked?.value ?? null,
    value_redacted: false,
    options: radios.map((r) => r.value),
    validation_message: null,
    nearby_text: groupLegend(first),
  };
}

const NON_FIELD_INPUT_TYPES = new Set(["submit", "button", "reset", "image"]);

/** Discover all actionable form fields, in document order. Hidden elements
 * (including honeypots) are excluded entirely. Radio inputs sharing a name
 * collapse into one radiogroup field. */
export function discoverFields(doc: Document): FormField[] {
  const controls = Array.from(
    doc.querySelectorAll<Control>("input, select, textarea"),
  );
  const fields: FormField[] = [];
  const seenRadioGroups = new Set<string>();

  controls.forEach((el, index) => {
    if (el instanceof HTMLInputElement && NON_FIELD_INPUT_TYPES.has(el.type)) return;
    if (!isVisible(el)) return;

    if (el instanceof HTMLInputElement && el.type === "radio" && el.name) {
      if (seenRadioGroups.has(el.name)) return;
      seenRadioGroups.add(el.name);
      const group = controls.filter(
        (c): c is HTMLInputElement =>
          c instanceof HTMLInputElement &&
          c.type === "radio" &&
          c.name === el.name &&
          isVisible(c),
      );
      fields.push(radioGroupField(group, el.name));
      return;
    }

    fields.push(baseField(el, fieldIdFor(el, index)));
  });

  return fields;
}
