import { elWindow, isInputEl, isSelectEl } from "./dom-types";
// Framework-compatible value setting: React/Vue track values via native
// property descriptors, so we must call the prototype setter and dispatch
// real bubbling events — assigning element.value directly is not seen by
// framework state (plan.md §9).

export function setNativeValue(
  el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
  value: string,
): void {
  const win = elWindow(el);
  const proto = isInputEl(el)
    ? win.HTMLInputElement.prototype
    : isSelectEl(el)
      ? win.HTMLSelectElement.prototype
      : win.HTMLTextAreaElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
}

export function fireInputEvents(el: HTMLElement): void {
  const win = elWindow(el);
  el.dispatchEvent(new win.Event("input", { bubbles: true }));
  el.dispatchEvent(new win.Event("change", { bubbles: true }));
}

export function focusThen(el: HTMLElement, mutate: () => void): void {
  const win = elWindow(el);
  el.dispatchEvent(new win.FocusEvent("focus", { bubbles: false }));
  if (typeof el.focus === "function") el.focus();
  mutate();
  el.dispatchEvent(new win.FocusEvent("blur", { bubbles: false }));
  if (typeof el.blur === "function") el.blur();
}
