import { describe, expect, it } from "vitest";
import { detectComboboxes } from "../../src/perception/widgets";
import { discoverFields } from "../../src/perception/fields";

const COMBO_HTML = `
  <label for="country">Country</label>
  <div class="combobox" data-combobox>
    <input id="country" role="combobox" aria-controls="clist" data-value-input="country">
    <input type="hidden" name="country" value="IN">
    <ul id="clist" role="listbox">
      <li role="option" data-value="DE">Germany</li>
      <li role="option" data-value="IN">India</li>
    </ul>
  </div>`;

describe("combobox detection", () => {
  it("reads options and current value from the listbox and hidden input", () => {
    document.body.innerHTML = COMBO_HTML;
    const combos = detectComboboxes(document);
    expect(combos).toHaveLength(1);
    expect(combos[0]!.options.map((o) => o.value)).toEqual(["DE", "IN"]);
    expect(combos[0]!.currentValue).toBe("IN");
  });

  it("surfaces a combobox as an option-bearing field", () => {
    document.body.innerHTML = COMBO_HTML;
    const field = discoverFields(document).find((f) => f.field_id === "country")!;
    expect(field.input_type).toBe("combobox");
    expect(field.options).toEqual(["DE", "IN"]);
    expect(field.target.role).toBe("combobox");
    expect(field.label).toBe("Country");
  });
});
