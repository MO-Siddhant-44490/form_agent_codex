// Realm-agnostic element checks. An element inside an iframe belongs to that
// frame's realm, so `el instanceof HTMLInputElement` (top realm) is false for
// it. tagName and the element's own defaultView are realm-safe.

export function elWindow(el: Element): Window & typeof globalThis {
  return (el.ownerDocument.defaultView as Window & typeof globalThis) ?? window;
}

export function isInputEl(el: Element): el is HTMLInputElement {
  return el.tagName === "INPUT";
}
export function isSelectEl(el: Element): el is HTMLSelectElement {
  return el.tagName === "SELECT";
}
export function isTextareaEl(el: Element): el is HTMLTextAreaElement {
  return el.tagName === "TEXTAREA";
}
export function isFormControl(
  el: Element,
): el is HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement {
  return isInputEl(el) || isSelectEl(el) || isTextareaEl(el);
}

export function isButtonEl(el: Element): el is HTMLButtonElement {
  return el.tagName === "BUTTON";
}
export function isDialogEl(el: Element): el is HTMLDialogElement {
  return el.tagName === "DIALOG";
}
/** An HTML element from ANY realm (an iframe's elements fail `instanceof HTMLElement`). */
export function isHtmlEl(node: unknown): node is HTMLElement {
  return (
    typeof node === "object" && node !== null && (node as Node).nodeType === 1 && "style" in (node as object)
  );
}
