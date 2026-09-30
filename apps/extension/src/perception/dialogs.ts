// Dialog / overlay detection with a safe dismiss target (Module 11, plan §9).
// Finds cookie/consent banners and modals, and the control to dismiss them —
// chosen from WITHIN the dialog only, so dismissal can never target the
// underlying form or grant a broad permission (Module 9 safety).
import type { PageObservation, TargetDescriptor } from "@form-agent/contracts";

type DialogInfo = NonNullable<PageObservation["dialogs"]>[number];
import { accessibleName, computedRole } from "./labels";
import { deepQueryAll } from "./shadow";
import { isVisible } from "./visibility";
import { isDialogEl, isInputEl } from "../actions/dom-types";

const COOKIE_RE = /\b(cookie|consent|gdpr|privacy|ccpa|tracking)\b/i;

// Dismiss-button text in priority order: prefer neutral/reject choices over
// "accept all", so dismissal is the conservative action (never grant more
// than necessary).
const NEUTRAL = /\b(close|dismiss|cancel|no thanks|not now|maybe later|skip)\b|^\s*[×✕✖x]\s*$/i;
// A generic modal is only ever closed with a neutral choice: "OK", "Yes",
// "Agree" or "Confirm" on a modal may CONFIRM something (a submission, a
// payment) — that is the user's decision.
const MODAL_PATTERNS: RegExp[] = [NEUTRAL];
// A cookie banner: reject first, then acknowledge, accept last.
const COOKIE_PATTERNS: RegExp[] = [
  NEUTRAL,
  /\b(reject all|reject|decline|necessary only|essential only|only necessary)\b/i,
  /\b(got it|ok|okay|understood|continue without)\b/i,
  /\b(accept all|accept|agree|allow all|allow|i agree)\b/i,
];

type Clickable = HTMLButtonElement | HTMLInputElement | HTMLAnchorElement | HTMLElement;

function dialogText(el: HTMLElement): string {
  return `${el.id} ${el.className} ${el.getAttribute("aria-label") ?? ""} ${el.textContent ?? ""}`;
}

function targetFor(el: Clickable, index: number): TargetDescriptor {
  const name =
    isInputEl(el) ? el.value || accessibleName(el) : accessibleName(el) || el.textContent?.trim() || null;
  return {
    field_id: el.id || `dialog-dismiss-${index}`,
    role: computedRole(el),
    accessible_name: name,
    input_type: isInputEl(el) ? el.type : null,
    label: name,
    name_attr: (el as HTMLInputElement).name || null,
    autocomplete: null,
    placeholder: null,
    bounding_box: null,
  };
}

/** The safest dismiss control inside a dialog, or null. Searches only within
 * the dialog element (never the page/form). */
function findDismiss(dialog: HTMLElement, index: number, cookie: boolean): TargetDescriptor | null {
  const buttons = deepQueryAll<Clickable>(
    dialog,
    'button, input[type="button"], input[type="submit"], [role="button"], a[href]',
  ).filter(isVisible);
  for (const pattern of cookie ? COOKIE_PATTERNS : MODAL_PATTERNS) {
    const match = buttons.find((b) => {
      const text = (isInputEl(b) ? b.value : b.textContent ?? "").trim();
      return pattern.test(text) || pattern.test(accessibleName(b) ?? "");
    });
    if (match) return targetFor(match, index);
  }
  return null;
}

export function detectDialogs(doc: Document): DialogInfo[] {
  const seen = new Set<HTMLElement>();
  const candidates: HTMLElement[] = [
    ...deepQueryAll<HTMLElement>(doc, "dialog[open], [role='dialog'], [role='alertdialog']"),
    // Cookie banners are usually plain containers, not role=dialog.
    ...deepQueryAll<HTMLElement>(
      doc,
      "[id*='cookie' i], [class*='cookie' i], [id*='consent' i], [class*='consent' i]",
    ),
  ];

  const dialogs: DialogInfo[] = [];
  candidates.forEach((el, i) => {
    if (seen.has(el)) return;
    // Skip a candidate nested inside one we already recorded.
    if (candidates.some((other) => other !== el && seen.has(other) && other.contains(el))) return;
    if (isDialogEl(el) && !el.open) return;
    if (!isVisible(el)) return;
    seen.add(el);

    const text = dialogText(el).toLowerCase();
    const kind = COOKIE_RE.test(text) ? "cookie_banner" : "modal";
    // A modal that holds visible, fillable controls IS the form (a "Register"
    // modal), not an overlay in the way of it.
    const containsForm = deepQueryAll<HTMLElement>(
      el,
      "input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]), select, textarea",
    ).some(isVisible);
    dialogs.push({
      dialog_id: el.id || `dialog-${i}`,
      kind,
      text_snippet: el.textContent?.trim().slice(0, 200) || null,
      dismiss_target: findDismiss(el, i, kind === "cookie_banner"),
      contains_form: containsForm,
    });
  });
  return dialogs;
}
