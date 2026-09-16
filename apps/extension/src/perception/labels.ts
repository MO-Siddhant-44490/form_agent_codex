// Label and accessible-name resolution, in the resilient-targeting order of
// plan.md §9: explicit label > wrapping label > aria > placeholder > title.

function textOf(el: Element | null): string | null {
  const text = el?.textContent?.trim();
  return text ? text : null;
}

function escapeAttr(value: string): string {
  // CSS.escape is not present in every DOM environment (e.g. jsdom); for an
  // attribute-value selector, escaping backslashes and quotes is sufficient.
  const cssEscape = globalThis.CSS?.escape;
  return cssEscape ? cssEscape(value) : value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

export function explicitLabel(el: HTMLElement): string | null {
  // Search the element's root (a shadow root, or the document) so a label
  // inside an open shadow root resolves correctly.
  const root = el.getRootNode() as Document | ShadowRoot;
  if (el.id) {
    const label = root.querySelector(`label[for="${escapeAttr(el.id)}"]`);
    const text = textOf(label);
    if (text) return text;
  }
  return textOf(el.closest("label"));
}

export function accessibleName(el: HTMLElement): string | null {
  const aria = el.getAttribute("aria-label")?.trim();
  if (aria) return aria;

  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const root = el.getRootNode() as Document | ShadowRoot;
    const byId = (id: string): Element | null =>
      root instanceof Document ? root.getElementById(id) : root.querySelector(`[id="${id}"]`);
    const parts = labelledBy
      .split(/\s+/)
      .map((id) => textOf(byId(id)))
      .filter((t): t is string => t !== null);
    if (parts.length > 0) return parts.join(" ");
  }

  const label = explicitLabel(el);
  if (label) return label;

  const placeholder = el.getAttribute("placeholder")?.trim();
  if (placeholder) return placeholder;

  const title = el.getAttribute("title")?.trim();
  return title || null;
}

export function groupLegend(el: HTMLElement): string | null {
  return textOf(el.closest("fieldset")?.querySelector("legend") ?? null);
}

const INPUT_ROLE: Record<string, string> = {
  text: "textbox",
  email: "textbox",
  tel: "textbox",
  url: "textbox",
  search: "searchbox",
  date: "textbox",
  "datetime-local": "textbox",
  time: "textbox",
  number: "spinbutton",
  range: "slider",
  checkbox: "checkbox",
  radio: "radio",
  file: "button",
  submit: "button",
  button: "button",
  reset: "button",
  password: "textbox",
};

export function computedRole(el: HTMLElement): string {
  const explicit = el.getAttribute("role")?.trim();
  if (explicit) return explicit;
  if (el instanceof HTMLInputElement) return INPUT_ROLE[el.type] ?? "textbox";
  if (el instanceof HTMLTextAreaElement) return "textbox";
  if (el instanceof HTMLSelectElement) return el.multiple ? "listbox" : "combobox";
  if (el instanceof HTMLButtonElement) return "button";
  return "generic";
}


/** Visible text sitting right next to a control (the common "<input> Male"
 * pattern with no <label>): the text after it up to the next control, else
 * the text before it, else its parent's text when the parent holds only this
 * one control. Used as a last-resort label for radios and checkboxes. */
export function adjacentText(el: HTMLElement): string | null {
  const isControl = (n: Node): boolean =>
    n instanceof HTMLElement && (n.matches("input, select, textarea, button") || n.querySelector("input, select, textarea, button") !== null);
  const collect = (start: Node | null, next: (n: Node) => Node | null): string => {
    const parts: string[] = [];
    for (let n = start; n; n = next(n)) {
      if (isControl(n)) break;
      const t = n.textContent?.replace(/\s+/g, " ").trim();
      if (t) parts.push(t);
      if (n instanceof HTMLElement && /^(BR|DIV|P|LI|TR|TD)$/.test(n.tagName)) break;
    }
    return parts.join(" ").trim();
  };
  const after = collect(el.nextSibling, (n) => n.nextSibling);
  if (after) return after;
  const before = collect(el.previousSibling, (n) => n.previousSibling);
  if (before) return before;
  const parent = el.parentElement;
  if (parent && parent.querySelectorAll("input, select, textarea").length === 1) {
    const t = parent.textContent?.replace(/\s+/g, " ").trim();
    if (t) return t;
  }
  return null;
}
