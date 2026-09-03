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
  const combos = deepQueryAll<HTMLElement>(doc, '[role="combobox"]')
    .filter(isVisible)
    // A combobox whose popup is a grid/dialog is a date picker or dialog
    // trigger, not a listbox combobox — leave it to the date-picker path.
    .filter((el) => {
      const pop = el.getAttribute("aria-haspopup");
      return pop === null || pop === "listbox" || pop === "menu" || pop === "true";
    });
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


// -- custom date pickers ---------------------------------------------------

export type DatePickerInfo = {
  element: HTMLElement;
  grid: HTMLElement | null;
  currentValue: string | null;
};

const _MONTHS: Record<string, number> = {
  jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
  jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
};

/** Parse a day-cell label like "April 17, 1998" or "17 Apr 1998" to ISO. */
export function parseCellDate(text: string): string | null {
  const t = text.trim();
  let m = /([A-Za-z]{3,})\.?\s+(\d{1,2}),?\s+(\d{4})/.exec(t); // April 17, 1998
  if (m) return isoFrom(m[3], m[1], m[2]);
  m = /(\d{1,2})\s+([A-Za-z]{3,})\.?\s+(\d{4})/.exec(t); // 17 April 1998
  if (m) return isoFrom(m[3], m[2], m[1]);
  return null;
}

function isoFrom(year: string, monthName: string, day: string): string | null {
  const month = _MONTHS[monthName.slice(0, 3).toLowerCase()];
  if (!month) return null;
  return `${year}-${String(month).padStart(2, "0")}-${String(Number(day)).padStart(2, "0")}`;
}

function pickerGrid(picker: HTMLElement): HTMLElement | null {
  const root = picker.getRootNode() as Document | ShadowRoot;
  const controls = picker.getAttribute("aria-controls") ?? picker.getAttribute("aria-owns");
  if (controls) {
    const byId =
      root instanceof Document ? root.getElementById(controls) : root.querySelector(`#${controls}`);
    if (byId) return byId as HTMLElement;
  }
  const container = picker.closest("[data-datepicker], .datepicker") ?? picker.parentElement;
  return (
    (container?.querySelector("[role='grid'], [role='dialog'], [role='application']") as HTMLElement) ??
    null
  );
}

export function detectDatePickers(doc: Document): DatePickerInfo[] {
  const pickers = deepQueryAll<HTMLElement>(
    doc,
    "input[data-datepicker], [role='combobox'][aria-haspopup='grid'], [role='combobox'][aria-haspopup='dialog']",
  ).filter(isVisible);
  return pickers.map((el) => {
    let currentValue: string | null = null;
    const hiddenName = el.getAttribute("data-value-input");
    if (hiddenName) {
      const root = el.getRootNode() as Document | ShadowRoot;
      currentValue = root.querySelector<HTMLInputElement>(`input[name="${hiddenName}"]`)?.value || null;
    } else if (el instanceof HTMLInputElement) {
      currentValue = el.value || null;
    }
    return { element: el, grid: pickerGrid(el), currentValue };
  });
}

/** Find the day cell in a calendar grid matching an ISO date (by data-date,
 * value, or a parseable aria-label / text). */
export function findDateCell(grid: HTMLElement, iso: string): HTMLElement | null {
  const cells = deepQueryAll<HTMLElement>(grid, "[role='gridcell'], [data-date], td, button");
  for (const cell of cells) {
    const direct = cell.getAttribute("data-date") ?? cell.getAttribute("data-value");
    if (direct === iso) return cell;
    const label = cell.getAttribute("aria-label") ?? cell.textContent ?? "";
    if (parseCellDate(label) === iso) return cell;
  }
  return null;
}
