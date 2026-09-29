// Role-based perception: controls are recognised by their ARIA ROLE and STATE
// (the same model screen readers use), not by how a site happens to build
// them. A <div role="radio" aria-checked> is a radio; a <div role="listbox">
// with role="option" children is a select; a contenteditable / role="textbox"
// div is a text field. Native inputs are perceived in ./fields; this module
// covers everything else that exposes a role — so a new site's custom widgets
// are understood without site-specific code.
//
// What still cannot be understood (an interactive role we have no executor
// for, or a click-driven div with no role at all) is REPORTED as an
// unrecognised control instead of being silently skipped.
import type { FormField, PageObservation, TargetDescriptor } from "@form-agent/contracts";
import { accessibleName } from "./labels";
import { classifyPurpose } from "./purpose";
import { deepQueryAll } from "./shadow";
import { isVisible } from "./visibility";

export const ARIA_PREFIX = "aria:";

type Unrecognized = NonNullable<PageObservation["unrecognized_controls"]>[number];

export type AriaWidget = { el: HTMLElement; field: FormField };

function text(el: Element | null | undefined): string | null {
  const t = el?.textContent?.replace(/\s+/g, " ").trim();
  return t || null;
}

/** The value an option/radio stands for: an explicit data value, else its name. */
export function optionValueOf(el: HTMLElement): string {
  return (
    el.getAttribute("data-value") ??
    el.getAttribute("data-answer-value") ??
    el.getAttribute("value") ??
    el.getAttribute("aria-label") ??
    text(el) ??
    ""
  );
}

function optionLabelOf(el: HTMLElement): string {
  return el.getAttribute("aria-label") ?? text(el) ?? optionValueOf(el);
}

/** The question a widget belongs to: its own accessible name, else the
 * heading/label of the container it sits in (a form's "question" block). */
export function widgetName(el: HTMLElement): string | null {
  const own = accessibleName(el);
  if (own) return own;
  const block = el.closest("[role='listitem'], [role='group'], fieldset, li, section, .question, .form-group");
  const heading = block?.querySelector("[role='heading'], legend, h1, h2, h3, h4, h5, label");
  return text(heading);
}

/** The question a *member* (one radio / one checkbox) belongs to. */
function groupName(el: HTMLElement): string | null {
  const group = el.closest("[role='radiogroup'], [role='group'], [role='list'], fieldset, [role='listitem']");
  return group ? widgetName(group as HTMLElement) : null;
}

function isRequired(el: HTMLElement): boolean {
  return el.getAttribute("aria-required") === "true" || el.hasAttribute("required");
}

function isDisabled(el: HTMLElement): boolean {
  return el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled");
}

function target(el: HTMLElement, fieldId: string, role: string, inputType: string, name: string | null): TargetDescriptor {
  return {
    field_id: fieldId,
    role,
    accessible_name: name,
    input_type: inputType,
    label: name,
    name_attr: el.getAttribute("name") || el.getAttribute("data-name") || null,
    autocomplete: el.getAttribute("autocomplete"),
    placeholder: el.getAttribute("placeholder") ?? el.getAttribute("aria-placeholder"),
    bounding_box: null,
  };
}

function base(el: HTMLElement, fieldId: string, role: string, inputType: string, name: string | null): FormField {
  const nearby = groupName(el);
  return {
    field_id: fieldId,
    target: target(el, fieldId, role, inputType, name),
    input_type: inputType,
    label: name,
    accessible_name: name,
    required: isRequired(el),
    disabled: isDisabled(el),
    readonly: el.getAttribute("aria-readonly") === "true",
    visible: true,
    checked: null,
    current_value: null,
    value_redacted: false,
    value_length: null,
    purpose: classifyPurpose({ inputType, autocomplete: el.getAttribute("autocomplete"), texts: [name, nearby, el.id] }),
    options: null,
    option_labels: null,
    validation_message: el.getAttribute("aria-invalid") === "true" ? "field marked invalid" : null,
    max_length: null,
    pattern: null,
    input_mode: null,
    nearby_text: nearby,
  };
}

// Native controls carry these roles implicitly; ./fields already perceives them.
// (A <button role="switch"> is a widget, so buttons are not excluded.)
const NATIVE = "input, select, textarea";

function nonNative(els: HTMLElement[]): HTMLElement[] {
  return els.filter((el) => !el.matches(NATIVE) && isVisible(el));
}

/** A stable-enough id for a widget without a DOM id: role + ordinal. Resolved
 * again at execution time by accessible name first, ordinal second. */
function widgetId(el: HTMLElement, role: string, index: number): string {
  return el.id || `${ARIA_PREFIX}${role}:${index}`;
}

export function detectAriaWidgets(doc: Document): { widgets: AriaWidget[]; unrecognized: Unrecognized[] } {
  const widgets: AriaWidget[] = [];
  const unrecognized: Unrecognized[] = [];
  const claimed = new Set<Element>();

  // Radio groups: one field per group, options = its radios.
  nonNative(deepQueryAll<HTMLElement>(doc, "[role='radiogroup']")).forEach((group, i) => {
    const radios = nonNative(deepQueryAll<HTMLElement>(group, "[role='radio']"));
    if (radios.length === 0) return;
    const name = widgetName(group);
    const checked = radios.find((r) => r.getAttribute("aria-checked") === "true");
    const field = base(group, widgetId(group, "radiogroup", i), "radiogroup", "radio", name);
    field.required = field.required || radios.some(isRequired);
    field.checked = checked !== undefined;
    field.current_value = checked ? optionValueOf(checked) : null;
    field.options = radios.map(optionValueOf);
    field.option_labels = radios.map(optionLabelOf);
    radios.forEach((r) => claimed.add(r));
    claimed.add(group);
    widgets.push({ el: group, field });
  });

  // Loose radios (no radiogroup): group by their question block.
  const loose = nonNative(deepQueryAll<HTMLElement>(doc, "[role='radio']")).filter((r) => !claimed.has(r));
  const byBlock = new Map<Element, HTMLElement[]>();
  for (const r of loose) {
    const block = r.closest("[role='group'], [role='listitem'], fieldset, form, body") ?? doc.body;
    byBlock.set(block, [...(byBlock.get(block) ?? []), r]);
  }
  let looseIndex = 0;
  for (const [block, radios] of byBlock) {
    const name = widgetName(block as HTMLElement);
    const checked = radios.find((r) => r.getAttribute("aria-checked") === "true");
    const field = base(block as HTMLElement, `${ARIA_PREFIX}radiogroup:loose:${looseIndex++}`, "radiogroup", "radio", name);
    field.required = radios.some(isRequired);
    field.checked = checked !== undefined;
    field.current_value = checked ? optionValueOf(checked) : null;
    field.options = radios.map(optionValueOf);
    field.option_labels = radios.map(optionLabelOf);
    radios.forEach((r) => claimed.add(r));
    widgets.push({ el: block as HTMLElement, field });
  }

  // Checkboxes and switches: one field each, named by their own label, with
  // the question they belong to as nearby text.
  nonNative(deepQueryAll<HTMLElement>(doc, "[role='checkbox'], [role='switch']"))
    .filter((el) => !claimed.has(el) && !el.closest("[role='option'], [role='listbox']"))
    .forEach((el, i) => {
      const role = el.getAttribute("role") === "switch" ? "switch" : "checkbox";
      const field = base(el, widgetId(el, role, i), role, "checkbox", accessibleName(el) ?? text(el));
      field.checked = el.getAttribute("aria-checked") === "true";
      claimed.add(el);
      widgets.push({ el, field });
    });

  // Listboxes that are not the popup of a combobox: a select whose options
  // may be hidden until it is opened (they are still in the DOM).
  nonNative(deepQueryAll<HTMLElement>(doc, "[role='listbox']"))
    .filter((el) => !el.closest("[role='combobox']") && !el.getAttribute("aria-multiselectable"))
    .forEach((el, i) => {
      const options = deepQueryAll<HTMLElement>(el, "[role='option']");
      if (options.length === 0) return;
      const selected = options.find((o) => o.getAttribute("aria-selected") === "true");
      const field = base(el, widgetId(el, "listbox", i), "listbox", "combobox", widgetName(el));
      const values = options.map(optionValueOf);
      field.options = values;
      field.option_labels = options.map(optionLabelOf);
      // A placeholder choice ("Choose") has an empty value: not a selection.
      field.current_value = selected && optionValueOf(selected) !== "" ? optionValueOf(selected) : null;
      options.forEach((o) => claimed.add(o));
      claimed.add(el);
      widgets.push({ el, field });
    });

  // Text fields built from divs: role="textbox" or contenteditable.
  nonNative(deepQueryAll<HTMLElement>(doc, "[role='textbox'], [contenteditable='true'], [contenteditable='']"))
    .filter((el) => !claimed.has(el) && !el.closest("[role='combobox']"))
    .forEach((el, i) => {
      const multiline = el.getAttribute("aria-multiline") === "true";
      const field = base(el, widgetId(el, "textbox", i), "textbox", multiline ? "textarea" : "text", widgetName(el));
      field.current_value = text(el);
      claimed.add(el);
      widgets.push({ el, field });
    });

  // Honest residue: interactive roles we can see but have no executor for.
  const OTHER_INTERACTIVE = "[role='slider'], [role='spinbutton'], [role='tree'], [role='grid'], [role='menu'], [role='tablist']";
  for (const el of nonNative(deepQueryAll<HTMLElement>(doc, OTHER_INTERACTIVE))) {
    if (claimed.has(el)) continue;
    unrecognized.push({ role: el.getAttribute("role") ?? "unknown", name: widgetName(el) });
  }

  return { widgets, unrecognized };
}
