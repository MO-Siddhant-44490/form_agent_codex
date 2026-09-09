// DOM/accessibility field discovery (Module 2). Pure functions over a
// Document; no page mutation, no model calls, no value capture for
// credential-like inputs (invariant 2).
import type { FormField, TargetDescriptor } from "@form-agent/contracts";
import { accessibleName, computedRole, explicitLabel, groupLegend } from "./labels";
import { deepQueryAll } from "./shadow";
import { detectComboboxes, detectDatePickers } from "./widgets";
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

function customValidationError(el: Control): string | null {
  // Custom validators (frameworks, jQuery Validate, ASP.NET, etc.) show the
  // message in a separate element, not el.validationMessage. Look for:
  // aria-describedby, aria-errormessage, or a nearby error element with text.
  const root = el.getRootNode() as Document | ShadowRoot;
  const byId = (id: string): Element | null =>
    root instanceof Document ? root.getElementById(id) : root.querySelector(`[id="${id}"]`);
  for (const attr of ["aria-errormessage", "aria-describedby"]) {
    const ref = el.getAttribute(attr);
    if (ref) {
      for (const id of ref.split(/\s+/)) {
        const node = byId(id);
        const text = node?.textContent?.trim();
        if (text && node && node.getAttribute("aria-hidden") !== "true") return text;
      }
    }
  }
  // A sibling/nearby element flagged as an error, with visible text.
  const container = el.closest("div, p, td, li, label, fieldset") ?? el.parentElement;
  const err = container?.querySelector(
    "[class*='error' i], [class*='invalid' i], [role='alert'], .field-validation-error",
  );
  if (err instanceof HTMLElement && isVisible(err)) {
    const text = err.textContent?.trim();
    if (text) return text;
  }
  return null;
}

function validationMessage(el: Control): string | null {
  // aria-invalid marks a field the site considers invalid regardless of value.
  const ariaInvalid = el.getAttribute("aria-invalid") === "true";
  const custom = customValidationError(el);
  if (custom) return custom;
  if (ariaInvalid) return "field marked invalid";
  // Deterministic local validity: only report once a value is in (an empty
  // required field is "not yet filled", not "invalid").
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
    option_labels: el instanceof HTMLSelectElement
      ? Array.from(el.options).map((o) => o.textContent?.trim() ?? o.value)
      : null,
    validation_message: credential ? null : validationMessage(el),
    max_length:
      (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) && el.maxLength >= 0
        ? el.maxLength
        : null,
    nearby_text: groupLegend(el),
  };
}

function currentValue(el: Control): string | null {
  if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
    return null; // represented by `checked`
  }
  if (el instanceof HTMLInputElement && el.type === "file") {
    // Report the selected file's name (not the fake C:\\fakepath prefix).
    const name = el.files?.[0]?.name;
    if (name) return name;
    const raw = el.value;
    return raw ? raw.split(/[\\/]/).pop() ?? raw : null;
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
    option_labels: null,
    validation_message: null,
    max_length: null,
    nearby_text: groupLegend(first),
  };
}

const NON_FIELD_INPUT_TYPES = new Set(["submit", "button", "reset", "image"]);

/** Discover all actionable form fields, in document order. Hidden elements
 * (including honeypots) are excluded entirely. Radio inputs sharing a name
 * collapse into one radiogroup field. */
export function discoverFields(doc: Document): FormField[] {
  const controls = deepQueryAll<Control>(doc, "input, select, textarea");
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

  mergeComboboxes(doc, fields);
  mergeDatePickers(doc, fields);
  return fields;
}

// Enrich discovered fields (or add new ones) for ARIA comboboxes so the
// planner sees them as option-bearing controls.
function mergeComboboxes(doc: Document, fields: FormField[]): void {
  for (const combo of detectComboboxes(doc)) {
    const el = combo.element;
    const backing = combo.backingSelect;
    // options = the values the executor selects; option_labels = human names,
    // so the mapper can match a name fact ("Maharashtra") to a code option ("MH").
    const options = combo.options.map((o) => o.value);
    const optionLabels = combo.options.map((o) => o.label || o.value);
    // Identity comes from the backing <select> when present (its name/id/label
    // are the real signal for mapping), else from the widget element.
    const name = backing?.name || el.getAttribute("data-value-input") || null;
    const label =
      (backing ? explicitLabel(backing) || accessibleName(backing) : null) ||
      explicitLabel(el) ||
      accessibleName(el);
    const id = backing?.id || el.id || name || `combobox-${fields.length}`;
    const existing = el.id ? fields.find((f) => f.field_id === el.id) : undefined;
    if (existing) {
      existing.input_type = "combobox";
      existing.options = options;
      existing.option_labels = optionLabels;
      existing.current_value = combo.currentValue;
      existing.label = label ?? existing.label;
      existing.accessible_name = label ?? existing.accessible_name;
      existing.required = existing.required || backing?.required === true;
      existing.target = {
        ...existing.target,
        field_id: id,
        role: "combobox",
        input_type: "combobox",
        label: label ?? existing.target.label,
        accessible_name: label ?? existing.target.accessible_name,
        name_attr: name ?? existing.target.name_attr,
      };
      existing.field_id = id;
    } else {
      fields.push({
        field_id: id,
        target: {
          field_id: id,
          role: "combobox",
          accessible_name: label,
          input_type: "combobox",
          label,
          name_attr: name,
          autocomplete: null,
          placeholder: el.getAttribute("placeholder"),
          bounding_box: boundingBox(el),
        },
        input_type: "combobox",
        label,
        accessible_name: label,
        required: backing?.required === true || el.getAttribute("aria-required") === "true",
        disabled: el.getAttribute("aria-disabled") === "true",
        readonly: false,
        visible: true,
        checked: null,
        current_value: combo.currentValue,
        value_redacted: false,
        options,
        option_labels: optionLabels,
        validation_message: null,
        max_length: null,
        nearby_text: groupLegend(el),
      });
    }
  }
}

// Enrich a custom date picker so it maps to a date fact and routes to the
// calendar-popup executor path.
function mergeDatePickers(doc: Document, fields: FormField[]): void {
  for (const picker of detectDatePickers(doc)) {
    const el = picker.element;
    const id = el.id || `datepicker-${fields.length}`;
    const nameAttr = el.getAttribute("data-value-input") ?? ((el as HTMLInputElement).name || null);
    const existing = el.id ? fields.find((f) => f.field_id === el.id) : undefined;
    if (existing) {
      existing.input_type = "date";
      existing.current_value = picker.currentValue;
      existing.readonly = false; // fillable via the calendar, not by typing
      existing.options = null; // a date picker is not an enumerated select
      existing.target = {
        ...existing.target,
        role: "datepicker",
        input_type: "date",
        name_attr: nameAttr ?? existing.target.name_attr,
      };
    } else {
      fields.push({
        field_id: id,
        target: {
          field_id: id, role: "datepicker", accessible_name: accessibleName(el),
          input_type: "date", label: explicitLabel(el), name_attr: nameAttr,
          autocomplete: null, placeholder: el.getAttribute("placeholder"),
          bounding_box: boundingBox(el),
        },
        input_type: "date", label: explicitLabel(el), accessible_name: accessibleName(el),
        required: el.getAttribute("aria-required") === "true",
        disabled: false, readonly: false, visible: true, checked: null,
        current_value: picker.currentValue, value_redacted: false, options: null, option_labels: null,
        validation_message: null, max_length: null, nearby_text: groupLegend(el),
      });
    }
  }
}
