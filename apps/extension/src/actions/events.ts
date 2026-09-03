// Framework-compatible value setting: React/Vue track values via native
// property descriptors, so we must call the prototype setter and dispatch
// real bubbling events — assigning element.value directly is not seen by
// framework state (plan.md §9).

export function setNativeValue(
  el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement,
  value: string,
): void {
  const proto =
    el instanceof HTMLInputElement
      ? HTMLInputElement.prototype
      : el instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLTextAreaElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
}

export function fireInputEvents(el: HTMLElement): void {
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

export function focusThen(el: HTMLElement, mutate: () => void): void {
  el.dispatchEvent(new FocusEvent("focus", { bubbles: false }));
  if (typeof el.focus === "function") el.focus();
  mutate();
  el.dispatchEvent(new FocusEvent("blur", { bubbles: false }));
  if (typeof el.blur === "function") el.blur();
}
