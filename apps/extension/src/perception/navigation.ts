// Navigation-control detection for multi-page forms (Module 11). Classifies
// buttons/submits by accessible name into next / previous / submit, and reads
// a "Step 2 of 4"-style progress label when present. Text is untrusted data;
// classification is heuristic and never an instruction (invariant 4).
import type { NavigationControl } from "@form-agent/contracts";
import { accessibleName, computedRole } from "./labels";
import { isVisible } from "./visibility";
import { isInputEl } from "../actions/dom-types";

const NEXT_RE = /\b(next|continue|proceed|forward|save\s*(&|and)?\s*continue)\b/i;
const PREV_RE = /\b(previous|back|prev|go\s*back)\b/i;
const SUBMIT_RE = /\b(submit|apply|finish|complete|confirm|send|review)\b/i;
const STEP_RE = /\bstep\s+(\d+)\s+of\s+(\d+)/i;
const DISMISS_RE = /\b(close|cancel|dismiss|no thanks|not now|maybe later)\b|^[×✕x]$/i;

/** Would clicking this control SUBMIT the form (the user's decision alone,
 * invariant 1)? True for a control named like submit/apply/confirm, or a
 * type=submit control that is not a next/previous/dismiss step. Used by the
 * executor to refuse any non-SUBMIT action that would land on one. */
export function submitsForm(el: Element): boolean {
  const name = candidateName(el as ButtonEl).trim();
  if (PREV_RE.test(name) || NEXT_RE.test(name) || DISMISS_RE.test(name)) return false;
  if (SUBMIT_RE.test(name)) return true;
  const type = (el.getAttribute("type") ?? "").toLowerCase();
  const isSubmitType =
    (el.tagName === "INPUT" && (type === "submit" || type === "image")) ||
    (el.tagName === "BUTTON" && (type === "" || type === "submit") && el.closest("form") !== null);
  return isSubmitType;
}

type ButtonEl = HTMLButtonElement | HTMLInputElement | HTMLAnchorElement;

function candidateName(el: ButtonEl): string {
  if (isInputEl(el)) return el.value || accessibleName(el) || "";
  return accessibleName(el) || el.textContent?.trim() || "";
}

export function detectNavigation(doc: Document): NavigationControl[] {
  const controls: NavigationControl[] = [];
  const seen = new Set<string>();
  const buttons = doc.querySelectorAll<ButtonEl>(
    'button, input[type="submit"], input[type="button"], [role="button"], a[href]',
  );
  buttons.forEach((el, index) => {
    if (!isVisible(el)) return;
    const name = candidateName(el);
    if (!name) return;
    let kind: NavigationControl["kind"] | null = null;
    // Order matters: "save & continue" is next, not submit; check prev/next
    // before submit so "review and submit" on a mid-form step still reads as
    // submit only when it is not a next-style control.
    if (PREV_RE.test(name)) kind = "previous";
    else if (NEXT_RE.test(name)) kind = "next";
    else if (SUBMIT_RE.test(name) || (isInputEl(el) && el.type === "submit"))
      kind = "submit";
    if (kind === null) return;

    const controlId = el.id || `nav-${kind}-${index}`;
    if (seen.has(controlId)) return;
    seen.add(controlId);
    controls.push({
      control_id: controlId,
      kind,
      label: name,
      target: {
        field_id: controlId,
        role: computedRole(el as HTMLElement),
        accessible_name: name,
        input_type: isInputEl(el) ? el.type : null,
        label: name,
        name_attr: (el as HTMLInputElement).name || null,
        autocomplete: null,
        placeholder: null,
        bounding_box: null,
      },
    });
  });
  return controls;
}

export function detectStepLabel(doc: Document): string | null {
  const text = doc.body?.textContent ?? "";
  const match = STEP_RE.exec(text);
  return match ? match[0] : null;
}

export function summarizeNavigation(
  doc: Document,
): { navigation: NavigationControl[]; step_label: string | null } {
  return { navigation: detectNavigation(doc), step_label: detectStepLabel(doc) };
}
