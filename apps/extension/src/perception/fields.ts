// DOM/accessibility field discovery (Module 2). Pure functions over a
// Document; no page mutation, no model calls, no value capture for
// credential-like inputs (invariant 2).
import type { FormField, TargetDescriptor } from "@form-agent/contracts";
import { accessibleName, adjacentText, computedRole, explicitLabel, groupLegend } from "./labels";
import { detectAriaWidgets } from "./aria";
import { classifyPurpose, type FieldPurpose } from "./purpose";
import { deepQueryAll } from "./shadow";
import { detectComboboxes, detectDatePickers } from "./widgets";
import { hasLayout, isVisible } from "./visibility";
import { isHtmlEl, isInputEl, isSelectEl, isTextareaEl } from "../actions/dom-types";

type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

/** What the control is for — decides credential redaction, captcha/consent
 * handling. Judged from the control's own text; see ./purpose. */
export function purposeOf(el: Control): FieldPurpose {
  return classifyPurpose({
    inputType: inputType(el),
    autocomplete: el.getAttribute("autocomplete"),
    texts: [
      explicitLabel(el),
      accessibleName(el),
      el.getAttribute("placeholder"),
      el.getAttribute("aria-label"),
      el.name,
      el.id,
      groupLegend(el),
    ],
  });
}

/** Values of credentials must never leave the page (invariant 2, threat T6).
 * A password-TYPE input is redacted whatever its purpose — a masked identifier
 * is fillable but its value is still not observed (only its length is). */
export function isRedactedControl(el: Control, purpose: FieldPurpose): boolean {
  return purpose === "credential" || (isInputEl(el) && el.type === "password");
}

function inputType(el: Control): string {
  if (isInputEl(el)) return el.type;
  if (isSelectEl(el)) return el.type; // select-one | select-multiple
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
  // aria-errormessage names an error outright; aria-describedby is only a
  // description (react-select points it at the placeholder) unless the control
  // is marked invalid.
  const invalid = el.getAttribute("aria-invalid") === "true";
  for (const attr of invalid ? ["aria-errormessage", "aria-describedby"] : ["aria-errormessage"]) {
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
  if (isHtmlEl(err) && isVisible(err)) {
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

// Validation libraries declare the same constraints as HTML, in their own
// attributes: ASP.NET unobtrusive (data-val-*), Parsley (data-parsley-*),
// jQuery Validate (data-rule-*), Angular (ng-pattern / ng-maxlength), and the
// generic data-pattern / data-maxlength. Reading them lets the agent fit a value
// BEFORE typing instead of learning the rule from a rejection.
const PATTERN_ATTRS = ["pattern", "data-val-regex-pattern", "data-parsley-pattern", "data-rule-pattern", "ng-pattern", "data-pattern"];
const MAXLEN_ATTRS = ["data-val-length-max", "data-val-maxlength-max", "data-parsley-maxlength", "data-rule-maxlength", "ng-maxlength", "data-maxlength"];

function declaredPattern(el: Control): string | null {
  if (isSelectEl(el)) return null;
  for (const attr of PATTERN_ATTRS) {
    const v = el.getAttribute(attr)?.trim();
    if (v) return v.replace(/^\/(.*)\/[a-z]*$/, "$1"); // "/^\d+$/" -> "^\d+$"
  }
  return null;
}

function declaredMaxLength(el: Control): number | null {
  if ((isInputEl(el) || isTextareaEl(el)) && el.maxLength >= 0) {
    return el.maxLength;
  }
  for (const attr of MAXLEN_ATTRS) {
    const n = Number.parseInt(el.getAttribute(attr) ?? "", 10);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return null;
}

function baseField(el: Control, fieldId: string): FormField {
  const purpose = purposeOf(el);
  const credential = purpose === "credential";
  const redacted = isRedactedControl(el, purpose);
  return {
    field_id: fieldId,
    target: descriptor(el, fieldId),
    input_type: inputType(el),
    label:
      explicitLabel(el) ??
      (isInputEl(el) && (el.type === "checkbox" || el.type === "radio")
        ? adjacentText(el)
        : null),
    accessible_name: accessibleName(el),
    required: el.required || el.getAttribute("aria-required") === "true",
    disabled: el.disabled,
    readonly: "readOnly" in el ? el.readOnly : false,
    visible: true,
    checked: isInputEl(el) && (el.type === "checkbox" || el.type === "radio")
      ? el.checked
      : null,
    current_value: redacted ? null : currentValue(el),
    value_redacted: redacted,
    // A masked identifier's length lets a fill be verified without the value.
    value_length: redacted && !credential ? (el.value ?? "").length : null,
    purpose,
    options: isSelectEl(el)
      ? Array.from(el.options).map((o) => o.value)
      : null,
    option_labels: isSelectEl(el)
      ? Array.from(el.options).map((o) => o.textContent?.trim() ?? o.value)
      : null,
    validation_message: credential ? null : validationMessage(el),
    max_length: declaredMaxLength(el),
    pattern: declaredPattern(el),
    input_mode: el.getAttribute("inputmode"),
    nearby_text: groupLegend(el),
  };
}

function currentValue(el: Control): string | null {
  if (isInputEl(el) && (el.type === "checkbox" || el.type === "radio")) {
    return null; // represented by `checked`
  }
  if (isInputEl(el) && el.type === "file") {
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
    value_length: null,
    purpose: purposeOf(first),
    options: radios.map((r) => r.value),
    // Human labels parallel to the values, so a fact "Female" can match a
    // radio whose value is the code "F".
    option_labels: radios.map((r) => explicitLabel(r) ?? accessibleName(r) ?? adjacentText(r) ?? r.value),
    validation_message: null,
    max_length: null,
    pattern: null,
    input_mode: null,
    nearby_text: groupLegend(first),
  };
}

const NON_FIELD_INPUT_TYPES = new Set(["submit", "button", "reset", "image"]);

/** Discover all actionable form fields, in document order. Hidden elements
 * (including honeypots) are excluded entirely. Radio inputs sharing a name
 * collapse into one radiogroup field. Native controls and role-based (ARIA)
 * widgets are interleaved in DOM order, so the fill proceeds top to bottom. */
export function discoverFields(doc: Document): FormField[] {
  return discoverFieldsWithReport(doc).fields;
}

export function discoverFieldsWithReport(doc: Document): {
  fields: FormField[];
  unrecognized: ReturnType<typeof detectAriaWidgets>["unrecognized"];
} {
  const controls = deepQueryAll<Control>(doc, "input, select, textarea");
  const entries: { el: Element; field: FormField }[] = [];
  const seenRadioGroups = new Set<string>();

  controls.forEach((el, index) => {
    if (isInputEl(el) && NON_FIELD_INPUT_TYPES.has(el.type)) return;
    if (!isVisible(el)) return;

    if (isInputEl(el) && el.type === "radio" && el.name) {
      if (seenRadioGroups.has(el.name)) return;
      seenRadioGroups.add(el.name);
      const group = controls.filter(
        (c): c is HTMLInputElement =>
          isInputEl(c) &&
          c.type === "radio" &&
          c.name === el.name &&
          isVisible(c),
      );
      entries.push({ el, field: radioGroupField(group, el.name) });
      return;
    }

    entries.push({ el, field: baseField(el, fieldIdFor(el, index)) });
  });

  const aria = detectAriaWidgets(doc);
  for (const w of aria.widgets) entries.push({ el: w.el, field: w.field });
  const fields = entries.map((e) => e.field);
  const elementOf = new Map<FormField, Element>(entries.map((e) => [e.field, e.el]));

  // Library widgets (Select2, date pickers) are merged in after discovery —
  // often wrapping a HIDDEN native control — so order must be decided after.
  mergeComboboxes(doc, fields, elementOf);
  mergeDatePickers(doc, fields, elementOf);

  // Document order across light DOM and open shadow roots (the same traversal
  // deepQueryAll uses), so the fill proceeds top to bottom as the user sees it.
  const all = deepQueryAll<Element>(doc, "*");
  const order = new Map(all.map((el, i) => [el, i] as const));
  const byId = new Map(all.filter((el) => el.id).map((el) => [el.id, el] as const));
  const position = (f: FormField): number => {
    const el = elementOf.get(f) ?? byId.get(f.field_id) ?? byId.get(f.target.field_id);
    return el ? (order.get(el) ?? Number.MAX_SAFE_INTEGER) : Number.MAX_SAFE_INTEGER;
  };
  const ranked = fields.map((f, i) => ({ f, i, pos: position(f) }));
  ranked.sort((a, b) => a.pos - b.pos || a.i - b.i);
  // Every field must be addressable on its own: id-less controls sharing a
  // name (a "lang" checkbox list) would otherwise all be "lang:checkbox".
  const seenIds = new Map<string, number>();
  for (const { f } of ranked) {
    const n = seenIds.get(f.field_id) ?? 0;
    seenIds.set(f.field_id, n + 1);
    if (n > 0) {
      f.field_id = `${f.field_id}#${n}`;
      f.target = { ...f.target, field_id: f.field_id };
    }
  }
  return { fields: ranked.map((r) => r.f), unrecognized: aria.unrecognized };
}

// Enrich discovered fields (or add new ones) for ARIA comboboxes so the
// planner sees them as option-bearing controls.
function mergeComboboxes(doc: Document, fields: FormField[], elementOf: Map<FormField, Element>): void {
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
    // The widget's own element may already be a native field (an input with
    // role=combobox): enrich that one — found by identity, not only by id —
    // rather than listing the same control twice.
    const existing = fields.find((f) => elementOf.get(f) === el) ?? (el.id ? fields.find((f) => f.field_id === el.id) : undefined);
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
        value_length: null,
        purpose: "standard",
        options,
        option_labels: optionLabels,
        validation_message: null,
        max_length: null,
        pattern: null,
        input_mode: null,
        nearby_text: groupLegend(el),
      });
    }
  }
}

// Enrich a custom date picker so it maps to a date fact and routes to the
// calendar-popup executor path.
function mergeDatePickers(doc: Document, fields: FormField[], elementOf: Map<FormField, Element>): void {
  for (const picker of detectDatePickers(doc)) {
    const el = picker.element;
    const id = el.id || `datepicker-${fields.length}`;
    const nameAttr = el.getAttribute("data-value-input") ?? ((el as HTMLInputElement).name || null);
    const existing = fields.find((f) => elementOf.get(f) === el) ?? (el.id ? fields.find((f) => f.field_id === el.id) : undefined);
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
        current_value: picker.currentValue, value_redacted: false, value_length: null,
        purpose: "standard", options: null, option_labels: null,
        validation_message: null, max_length: null, pattern: null, input_mode: null,
        nearby_text: groupLegend(el),
      });
    }
  }
}
