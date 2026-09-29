// Deterministic executor (Module 3): applies exactly one validated typed
// action to the live DOM. No model output executes directly (invariant 5);
// every mutation goes through framework-compatible native events.
import type { ActionResult, BrowserAction } from "@form-agent/contracts";
import {
  detectComboboxes,
  detectDatePickers,
  findDateCell,
  findMonthNav,
  shownMonth,
} from "../perception/widgets";
import { optionValueOf } from "../perception/aria";
import { deepQueryAll } from "../perception/shadow";
import { isVisible } from "../perception/visibility";
import { elWindow, isFormControl, isInputEl, isSelectEl } from "./dom-types";
import { fireInputEvents, focusThen, setNativeValue } from "./events";
import { resolveTarget, type Resolved } from "./resolve";
import { spotlight } from "./spotlight";

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
    failure_class: null,
    executed_at: new Date().toISOString(),
    ...extra,
  };
}

function failed(
  action: BrowserAction,
  error: string,
  failureClass: ActionResult["failure_class"] = null,
): ActionResult {
  return result(action, "FAILED", { error, failure_class: failureClass });
}

const TEXTUAL_KINDS = new Set(["SET_TEXT", "SET_NUMBER", "SET_DATE", "SELECT_OPTION"]);
// Actions that fill a field (get the on-page spotlight); not clicks/navigation.
const FILL_KINDS = new Set([...TEXTUAL_KINDS, "SET_CHECKBOX", "SET_RADIO"]);

export async function executeAction(doc: Document, action: BrowserAction): Promise<ActionResult> {
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
      return await executeTargeted(doc, action);
    case "SCROLL": {
      doc.defaultView?.scrollBy({ top: doc.defaultView.innerHeight * 0.8 });
      return result(action, "EXECUTED");
    }
    case "WAIT_FOR_STABLE_PAGE":
      // The stability wait happens in the content-script command loop before
      // this returns; by the time we are here the DOM was quiet.
      return result(action, "EXECUTED");
    case "UPLOAD_FILE":
      return executeUpload(doc, action);
  }
}

async function executeTargeted(doc: Document, action: BrowserAction): Promise<ActionResult> {
  if (!action.target) return failed(action, "action has no target");

  // Custom ARIA combobox: open the popup and click the matching option,
  // rather than treating it as a native <select>. (A role="listbox" select is
  // a role-based widget, handled below by role.)
  if (
    action.kind === "SELECT_OPTION" &&
    action.target.input_type === "combobox" &&
    action.target.role !== "listbox"
  ) {
    return await executeCombobox(doc, action);
  }
  // Custom calendar date picker: open it and click the matching day cell.
  if (action.kind === "SET_DATE" && action.target.role === "datepicker") {
    return executeDatePicker(doc, action);
  }

  const resolved = resolveTarget(doc, action.target);
  if (resolved.kind === "not-found") return failed(action, resolved.detail);
  if (FILL_KINDS.has(action.kind)) {
    spotlight(
      resolved.kind === "radio-group"
        ? (resolved.radios[0]?.closest("fieldset, [role=radiogroup], .form-group, div") ?? resolved.radios[0])
        : resolved.element,
    );
  }
  if (resolved.kind === "aria-widget") return await executeAriaWidget(doc, action, resolved);

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
      return failed(action, `option ${value} not present`, "option_not_found");
    }
    const alternate = action.method_hint === "alternate";
    focusThen(el, () => {
      if (alternate) setNativeValue(el, ""); // clear first so frameworks see a real change
      setNativeValue(el, value);
      fireInputEvents(el);
    });
    if (alternate) {
      // A second, different signal: on-blur validators/committers that ignored
      // the programmatic set get another chance to pick up the value.
      el.dispatchEvent(new (elWindow(el)).FocusEvent("blur", { bubbles: true }));
    }
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
    if (!match) return failed(action, `no radio with value ${value}`, "option_not_found");
    if (!match.checked) match.click();
    return result(action, "EXECUTED");
  }

  // CLICK / DISMISS_DIALOG / SUBMIT: a real click on the resolved element.
  if (resolved.kind !== "element") return failed(action, "click needs a single element");
  (resolved.element as HTMLElement).click();
  return result(action, "EXECUTED");
}

function executeUpload(doc: Document, action: BrowserAction): ActionResult {
  if (!action.target) return failed(action, "action has no target");
  const upload = action.upload_file;
  if (!upload) return failed(action, "UPLOAD_FILE has no file reference");
  const resolved = resolveTarget(doc, action.target);
  if (resolved.kind !== "element") return failed(action, "file input not found", "element_not_found");
  const el = resolved.element;
  if (!isInputEl(el) || el.type !== "file") return failed(action, "target is not a file input");
  if (el.disabled) return failed(action, "file input is disabled");

  const win = elWindow(el);
  // Decode base64 -> bytes in the element's realm and set the input's files via
  // DataTransfer (the only content-script way to programmatically attach a file).
  let buffer: ArrayBuffer;
  try {
    const binary = win.atob(upload.content_base64);
    buffer = new ArrayBuffer(binary.length);
    const view = new Uint8Array(buffer);
    for (let i = 0; i < binary.length; i++) view[i] = binary.charCodeAt(i);
  } catch {
    return failed(action, "invalid base64 content");
  }
  const file = new win.File([buffer], upload.filename, { type: upload.mime_type });
  const dt = new win.DataTransfer();
  dt.items.add(file);
  el.files = dt.files;
  el.dispatchEvent(new win.Event("input", { bubbles: true }));
  el.dispatchEvent(new win.Event("change", { bubbles: true }));
  return result(action, "EXECUTED");
}

function executeDatePicker(doc: Document, action: BrowserAction): ActionResult {
  const value = action.resolved_value;
  if (value === null || value === undefined) return failed(action, "no resolved value");
  const pickers = detectDatePickers(doc);
  const picker =
    pickers.find((p) => p.element.id === action.target!.field_id) ??
    (pickers.length === 1 ? pickers[0] : undefined);
  if (!picker) return failed(action, "date picker not found", "element_not_found");

  const el = picker.element;
  const win = elWindow(el);
  el.dispatchEvent(new win.FocusEvent("focus", { bubbles: false }));
  if (typeof el.focus === "function") el.focus();
  el.click();
  el.setAttribute("aria-expanded", "true");

  const gridOf = () => detectDatePickers(doc).find((p) => p.element === el)?.grid ?? null;
  let grid = gridOf();
  if (!grid) return failed(action, "date picker has no calendar grid", "element_not_found");

  let cell = findDateCell(grid, value);
  if (!cell) {
    // Navigate months toward the target, bounded, re-scanning after each click
    // (the grid usually re-renders).
    const [ty, tm] = [Number(value.slice(0, 4)), Number(value.slice(5, 7))];
    const nav = findMonthNav(el);
    for (let guard = 0; guard < 36 && !cell; guard++) {
      grid = gridOf();
      if (!grid) break;
      cell = findDateCell(grid, value);
      if (cell) break;
      const shown = shownMonth(grid);
      if (!shown) break;
      const delta = (ty - shown.year) * 12 + (tm - shown.month);
      const btn = delta > 0 ? nav.next : delta < 0 ? nav.prev : null;
      if (!btn) break;
      btn.click();
    }
  }
  if (!cell) return failed(action, `no calendar cell for ${value} after month navigation`, "option_not_found");
  cell.click();
  return result(action, "EXECUTED");
}

// Drive the Select2 UI: open the widget and click the rendered result whose
// text matches the option label. Firing the real click makes Select2 emit its
// own events, which is what triggers the site's cascading loads (e.g. picking
// a state loads its districts). Returns null if the UI list did not render
// (e.g. options load via AJAX) so the caller can fall back.
async function clickSelect2Option(
  doc: Document,
  el: HTMLElement,
  label: string,
  action: BrowserAction,
): Promise<ActionResult | null> {
  const win = elWindow(el);
  // Select2 toggles OPEN on mousedown of the selection. Do NOT also fire
  // mouseup/click here — that would toggle it closed again.
  if (typeof el.focus === "function") el.focus();
  el.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true, cancelable: true }));

  // Select2 renders its result list asynchronously; wait for it to appear.
  const target = label.trim().toLowerCase();
  let results: HTMLElement[] = [];
  for (let i = 0; i < 20; i++) {
    results = deepQueryAll<HTMLElement>(doc, ".select2-results__option").filter(isVisible);
    if (results.length > 0) break;
    await new Promise((r) => win.setTimeout(r, 50));
  }
  if (results.length === 0) return null; // nothing rendered -> fall back
  const match =
    results.find((o) => (o.textContent?.trim().toLowerCase() ?? "") === target) ??
    results.find((o) => (o.textContent?.trim().toLowerCase() ?? "").startsWith(target));
  if (!match) return null; // option not in the rendered list -> fall back
  // Select2 selects a result on mouseup.
  match.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true, cancelable: true }));
  match.dispatchEvent(new win.MouseEvent("mouseup", { bubbles: true, cancelable: true }));
  // Select2 normally collapses on select, but a cascade re-render can leave the
  // dropdown open over the page. If it's still open, close it explicitly (an
  // outside pointer-down / Escape) so only the chosen value shows.
  await new Promise((r) => win.setTimeout(r, 40));
  if (deepQueryAll<HTMLElement>(doc, ".select2-container--open").length > 0) {
    el.dispatchEvent(
      new win.KeyboardEvent("keydown", { bubbles: true, key: "Escape", keyCode: 27 }),
    );
    doc.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    doc.dispatchEvent(new win.MouseEvent("mouseup", { bubbles: true, cancelable: true }));
  }
  return result(action, "EXECUTED");
}

// -- role-based widgets ------------------------------------------------------
// Execution by ROLE: a checkbox/switch/radio is clicked; a listbox is opened
// and its option clicked; a textbox gets its text set. The same code drives any
// site that exposes roles, whatever framework built the widget.

const norm = (s: string): string => s.trim().toLowerCase();

function matchMember(members: HTMLElement[], value: string): HTMLElement | undefined {
  return (
    members.find((m) => optionValueOf(m) === value) ??
    members.find((m) => norm(optionValueOf(m)) === norm(value)) ??
    members.find((m) => norm(m.getAttribute("aria-label") ?? m.textContent ?? "") === norm(value))
  );
}

function realClick(el: HTMLElement): void {
  const win = elWindow(el);
  el.scrollIntoView?.({ block: "center" });
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
    const Ctor = type.startsWith("pointer") && "PointerEvent" in win ? win.PointerEvent : win.MouseEvent;
    el.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: true, composed: true }));
  }
}

async function executeAriaWidget(
  doc: Document,
  action: BrowserAction,
  resolved: Extract<Resolved, { kind: "aria-widget" }>,
): Promise<ActionResult> {
  const { role, element, members } = resolved;
  const value = action.resolved_value;

  if (role === "radiogroup") {
    if (value === null || value === undefined) return failed(action, "no resolved value");
    const match = matchMember(members, value);
    if (!match) return failed(action, `no radio for ${value}`, "option_not_found");
    if (match.getAttribute("aria-checked") !== "true") realClick(match);
    return result(action, "EXECUTED");
  }

  if (role === "checkbox" || role === "switch") {
    const desired = action.expected_effect?.checked;
    if (desired === null || desired === undefined) return failed(action, "no desired checked state");
    if ((element.getAttribute("aria-checked") === "true") !== desired) realClick(element);
    return result(action, "EXECUTED");
  }

  if (role === "listbox") {
    if (value === null || value === undefined) return failed(action, "no resolved value");
    // Open the list (the options may be rendered/visible only after that),
    // then click the matching option; re-query in case the site re-renders.
    realClick(element);
    await new Promise((r) => setTimeout(r, 150));
    const options = [...members, ...deepQueryAll<HTMLElement>(doc, "[role='option']")].filter(
      (o, i, all) => all.indexOf(o) === i,
    );
    const match = matchMember(options, value);
    if (!match) return failed(action, `no option for ${value}`, "option_not_found");
    realClick(match);
    return result(action, "EXECUTED");
  }

  if (role === "textbox") {
    if (value === null || value === undefined) return failed(action, "no resolved value");
    const win = elWindow(element);
    element.focus();
    element.textContent = value;
    element.dispatchEvent(new win.InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
    element.dispatchEvent(new win.Event("change", { bubbles: true }));
    element.dispatchEvent(new win.FocusEvent("blur", { bubbles: true }));
    return result(action, "EXECUTED");
  }

  return failed(action, `no executor for role ${role}`);
}

async function executeCombobox(doc: Document, action: BrowserAction): Promise<ActionResult> {
  const value = action.resolved_value;
  if (value === null || value === undefined) return failed(action, "no resolved value");
  const combos = detectComboboxes(doc);
  const fid = action.target!.field_id;
  const nm = action.target!.name_attr;
  const combo =
    combos.find(
      (c) =>
        c.element.id === fid ||
        c.backingSelect?.id === fid ||
        (nm !== null && c.backingSelect?.name === nm),
    ) ?? (combos.length === 1 ? combos[0] : undefined);
  if (!combo) return failed(action, `combobox not found for ${fid}`, "element_not_found");

  const el = combo.element;
  const win = elWindow(el);
  spotlight(el);

  // Custom dropdowns (Select2/Chosen/selectize) are backed by a hidden native
  // <select>.
  if (combo.backingSelect) {
    const select = combo.backingSelect;
    const target = value.trim().toLowerCase();
    const matchOption =
      Array.from(select.options).find((o) => o.value === value) ??
      Array.from(select.options).find((o) => o.value.trim().toLowerCase() === target) ??
      Array.from(select.options).find((o) => (o.textContent?.trim().toLowerCase() ?? "") === target);
    if (!matchOption) return failed(action, `no option ${value} in backing select`, "option_not_found");
    const label = matchOption.textContent?.trim() ?? matchOption.value;

    // Prefer driving the widget's own UI: open it and click the rendered
    // option, so the SITE's handlers fire (Select2's select event) and any
    // dependent/cascading load (state -> district) is triggered. Fall back to
    // setting the hidden select directly if the UI list does not render.
    const preferUi = action.method_hint === "widget_ui";
    if (el.classList.contains("select2-selection") || el.closest(".select2-container")) {
      const uiResult = await clickSelect2Option(doc, el, label, action);
      if (uiResult) return uiResult;
      // ALT_SELECT recovery: the native fallback is what failed to fire the
      // site's handlers last time, so don't silently repeat it — report that
      // the widget UI did not offer the option so recovery can escalate.
      if (preferUi) return failed(action, `widget UI did not offer ${label}`, "option_not_found");
    }
    setNativeValue(select, matchOption.value);
    select.dispatchEvent(new win.Event("input", { bubbles: true }));
    select.dispatchEvent(new win.Event("change", { bubbles: true }));
    return result(action, "EXECUTED");
  }

  // Open the popup (focus + click) so the listbox renders.
  el.dispatchEvent(new win.FocusEvent("focus", { bubbles: false }));
  if (typeof el.focus === "function") el.focus();
  el.click();
  el.setAttribute("aria-expanded", "true");

  // Re-scan options now that the popup is open, then click the match.
  const fresh = detectComboboxes(doc).find((c) => c.element === el) ?? combo;
  const listbox = fresh.listbox;
  if (!listbox) return failed(action, "combobox has no listbox", "element_not_found");
  const options = deepQueryAll<HTMLElement>(listbox, '[role="option"]');
  const target = value.trim().toLowerCase();
  const optionKey = (o: HTMLElement) =>
    (o.getAttribute("data-value") ?? o.getAttribute("value") ?? o.textContent?.trim() ?? "");
  // Exact first, then case-insensitive on value or visible text.
  const match =
    options.find((o) => optionKey(o) === value) ??
    options.find((o) => optionKey(o).trim().toLowerCase() === target) ??
    options.find((o) => (o.textContent?.trim().toLowerCase() ?? "") === target);
  if (!match) return failed(action, `no combobox option for ${value}`, "option_not_found");
  match.click();
  return result(action, "EXECUTED");
}
