// Custom widget detection (Module 11). Focuses on the ARIA combobox pattern
// (a searchable dropdown): an input/element with role="combobox" whose options
// live in an associated listbox. Options and the current selection are read so
// the field looks like a native select to the planner.
import { deepQueryAll } from "./shadow";
import { isVisible } from "./visibility";

export type ComboboxInfo = {
  element: HTMLElement;
  listbox: HTMLElement | null;
  options: { value: string; label: string }[];
  currentValue: string | null;
};

function optionValue(option: HTMLElement): string {
  return (
    option.getAttribute("data-value") ??
    option.getAttribute("value") ??
    option.textContent?.trim() ??
    ""
  );
}

/** The listbox a combobox controls: aria-controls / aria-owns, else the first
 * [role="listbox"] within the same widget container. */
function findListbox(combo: HTMLElement): HTMLElement | null {
  const root = combo.getRootNode() as Document | ShadowRoot;
  const controls = combo.getAttribute("aria-controls") ?? combo.getAttribute("aria-owns");
  if (controls) {
    const byId =
      root instanceof Document ? root.getElementById(controls) : root.querySelector(`#${controls}`);
    if (byId) return byId as HTMLElement;
  }
  const container = combo.closest("[role='combobox'], .combobox, [data-combobox]") ?? combo.parentElement;
  return (container?.querySelector("[role='listbox']") as HTMLElement) ?? null;
}

export function detectComboboxes(doc: Document): ComboboxInfo[] {
  const combos = deepQueryAll<HTMLElement>(doc, '[role="combobox"]').filter(isVisible);
  return combos.map((combo) => {
    const listbox = findListbox(combo);
    const options = listbox
      ? deepQueryAll<HTMLElement>(listbox, '[role="option"]').map((o) => ({
          value: optionValue(o),
          label: o.textContent?.trim() ?? "",
        }))
      : [];
    // Current value: a linked hidden input, aria-activedescendant, or the
    // combobox's own text value.
    let currentValue: string | null = null;
    const hiddenName = combo.getAttribute("data-value-input");
    if (hiddenName) {
      const root = combo.getRootNode() as Document | ShadowRoot;
      const hidden = root.querySelector<HTMLInputElement>(`input[name="${hiddenName}"]`);
      currentValue = hidden?.value || null;
    } else if (combo instanceof HTMLInputElement) {
      currentValue = combo.value || null;
    }
    return { element: combo, listbox, options, currentValue };
  });
}
