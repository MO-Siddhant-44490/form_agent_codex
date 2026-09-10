// Resilient target resolution in the plan.md §9 order: DOM id -> name ->
// accessible name among role-compatible controls. Raw CSS/XPath from a model
// is never accepted (invariant 5) — only TargetDescriptor fields are used.
import type { TargetDescriptor } from "@form-agent/contracts";
import { ARIA_PREFIX, widgetName } from "../perception/aria";
import { accessibleName } from "../perception/labels";
import { deepGetById, deepQueryAll } from "../perception/shadow";
import { isVisible } from "../perception/visibility";

type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

const RADIO_GROUP_PREFIX = "radio-group:";

export type Resolved =
  | { kind: "element"; element: Control | HTMLElement }
  | { kind: "radio-group"; radios: HTMLInputElement[] }
  // A role-based widget (div-built): the element carrying the role, plus its
  // members for a group (role="radio" children of a radiogroup).
  | { kind: "aria-widget"; role: string; element: HTMLElement; members: HTMLElement[] }
  | { kind: "not-found"; detail: string };

const ARIA_ROLES = new Set(["radiogroup", "checkbox", "switch", "listbox", "textbox"]);

/** Re-find a role-based widget in a fresh DOM: by id, then by role + question
 * name, then by the ordinal captured at observation time. */
function resolveAriaWidget(doc: Document, target: TargetDescriptor): Resolved {
  const role = target.role;
  const selector = role === "radiogroup" ? "[role='radiogroup']" : role === "textbox"
    ? "[role='textbox'], [contenteditable='true'], [contenteditable='']" : `[role='${role}']`;
  const candidates = deepQueryAll<HTMLElement>(doc, selector).filter(
    (c) => isVisible(c) && !c.matches("input, select, textarea"),
  );
  let el: HTMLElement | undefined;
  if (!target.field_id.startsWith(ARIA_PREFIX)) el = candidates.find((c) => c.id === target.field_id);
  if (!el && target.accessible_name) {
    const named = candidates.filter((c) => widgetName(c) === target.accessible_name);
    if (named.length === 1) el = named[0];
  }
  if (!el) {
    const ordinal = Number(target.field_id.split(":").pop());
    if (Number.isInteger(ordinal)) el = candidates[ordinal];
  }
  if (!el && target.field_id.includes(":loose:")) {
    // Loose radios grouped by their question block: find the block by name.
    const radios = deepQueryAll<HTMLElement>(doc, "[role='radio']").filter(
      (r) => isVisible(r) && !r.closest("[role='radiogroup']") && widgetName(
        (r.closest("[role='group'], [role='listitem'], fieldset, form, body") ?? doc.body) as HTMLElement,
      ) === target.accessible_name,
    );
    if (radios.length > 0) return { kind: "aria-widget", role: "radiogroup", element: radios[0]!, members: radios };
  }
  if (!el) return { kind: "not-found", detail: `no ${role} widget for ${target.field_id}` };
  const members =
    role === "radiogroup"
      ? deepQueryAll<HTMLElement>(el, "[role='radio']").filter(isVisible)
      : role === "listbox"
        ? deepQueryAll<HTMLElement>(el, "[role='option']")
        : [];
  return { kind: "aria-widget", role, element: el, members };
}

export function resolveTarget(doc: Document, target: TargetDescriptor): Resolved {
  // Role-based (div-built) widgets are addressed by role, not by input type.
  if (ARIA_ROLES.has(target.role) && !target.field_id.startsWith(RADIO_GROUP_PREFIX)) {
    const byRole = resolveAriaWidget(doc, target);
    if (byRole.kind !== "not-found") return byRole;
  }

  // Radio groups are addressed by group, selected by value at execution time.
  if (target.field_id.startsWith(RADIO_GROUP_PREFIX)) {
    const name = target.field_id.slice(RADIO_GROUP_PREFIX.length);
    const radios = deepQueryAll<HTMLInputElement>(doc, 'input[type="radio"]').filter(
      (r) => r.name === name && isVisible(r),
    );
    return radios.length > 0
      ? { kind: "radio-group", radios }
      : { kind: "not-found", detail: `no visible radios named ${name}` };
  }

  // 1. DOM id (stable ids captured at observation time), searched across
  // open shadow roots and same-origin iframes.
  const byId = deepGetById(doc, target.field_id);
  if (byId && isVisible(byId)) return { kind: "element", element: byId as Control };

  // 2. name attribute + input type.
  if (target.name_attr) {
    const candidates = deepQueryAll<Control>(doc, "input, select, textarea").filter(
      (el) => el.name === target.name_attr && isVisible(el),
    );
    if (candidates.length === 1) return { kind: "element", element: candidates[0]! };
  }

  // 3. accessible name among visible controls of a compatible input type.
  if (target.accessible_name) {
    const candidates = deepQueryAll<Control>(doc, "input, select, textarea").filter(
      (el) =>
        isVisible(el) &&
        accessibleName(el) === target.accessible_name &&
        (target.input_type === null || inputTypeOf(el) === target.input_type),
    );
    if (candidates.length === 1) return { kind: "element", element: candidates[0]! };
    if (candidates.length > 1) {
      return { kind: "not-found", detail: `ambiguous accessible name ${target.accessible_name}` };
    }
  }

  // 4. Clickable targets (buttons, links, submit inputs) — used by CLICK,
  // NAVIGATE_NEXT, DISMISS_DIALOG. Navigation controls often have no stable
  // id, so match by accessible name / visible text.
  if (target.role === "button" || target.input_type === "submit" || target.accessible_name) {
    const clickable = deepQueryAll<HTMLElement>(
      doc,
      'button, input[type="submit"], input[type="button"], [role="button"], a[href]',
    ).filter((el) => {
      if (!isVisible(el)) return false;
      const name =
        el instanceof HTMLInputElement
          ? el.value || accessibleName(el)
          : accessibleName(el) || el.textContent?.trim();
      return name === target.accessible_name;
    });
    if (clickable.length >= 1) return { kind: "element", element: clickable[0]! };
  }

  return { kind: "not-found", detail: `no element for ${target.field_id}` };
}

function inputTypeOf(el: Control): string {
  return el instanceof HTMLInputElement || el instanceof HTMLSelectElement
    ? el.type
    : "textarea";
}
