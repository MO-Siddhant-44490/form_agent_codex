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
  it("reads options and current value from the listbox and hidden input", async () => {
    document.body.innerHTML = COMBO_HTML;
    const combos = detectComboboxes(document);
    expect(combos).toHaveLength(1);
    expect(combos[0]!.options.map((o) => o.value)).toEqual(["DE", "IN"]);
    expect(combos[0]!.currentValue).toBe("IN");
  });

  it("surfaces a combobox as an option-bearing field", async () => {
    document.body.innerHTML = COMBO_HTML;
    const field = discoverFields(document).find((f) => f.field_id === "country")!;
    expect(field.input_type).toBe("combobox");
    expect(field.options).toEqual(["DE", "IN"]);
    expect(field.target.role).toBe("combobox");
    expect(field.label).toBe("Country");
  });
});

import { executeAction } from "../../src/actions/execute";
import { findBackingSelect } from "../../src/perception/widgets";

describe("Select2-style combobox backed by a hidden native select", () => {
  const SELECT2_HTML = `
    <label for="state-label">State</label>
    <select class="select2-hidden-accessible" name="state" id="state-select" style="display:none">
      <option value="">--Select--</option>
      <option value="MH">Maharashtra</option>
      <option value="GJ">Gujarat</option>
    </select>
    <span class="select2-container">
      <span id="state-combo" role="combobox" aria-haspopup="true" data-value-input="state">--Select--</span>
    </span>`;

  it("detects the backing select and reads its options", async () => {
    document.body.innerHTML = SELECT2_HTML;
    const combo = document.getElementById("state-combo")!;
    expect(findBackingSelect(combo)?.id).toBe("state-select");
    const info = detectComboboxes(document)[0]!;
    expect(info.options.map((o) => o.value)).toEqual(["MH", "GJ"]);
  });

  it("fills a Select2 combobox by driving the hidden select and firing change", async () => {
    document.body.innerHTML = SELECT2_HTML;
    const select = document.getElementById("state-select") as HTMLSelectElement;
    let changed = false;
    select.addEventListener("change", () => { changed = true; });

    const action = {
      action_id: "a-1", run_id: "r", tab_id: 1, origin: "http://localhost:3000",
      sequence_number: 1, kind: "SELECT_OPTION",
      target: { field_id: "state-combo", role: "combobox", accessible_name: "State",
        input_type: "combobox", label: "State", name_attr: "state", autocomplete: null,
        placeholder: null, bounding_box: null },
      value_ref: null, resolved_value: "MH",
      expected_effect: { field_value: "MH", checked: null, selected_option: null,
        validation_error: false, dialog_dismissed: null, navigation_expected: null,
        expected_url_prefix: null },
      risk: "low", idempotency_key: "k", source_observation_seq: null,
      approval_token_id: null, upload_file: null, method_hint: null,
    } as const;

    const result = await executeAction(document, action);
    expect(result.status).toBe("EXECUTED");
    expect(select.value).toBe("MH");        // hidden select was driven
    expect(changed).toBe(true);              // change fired (triggers cascading loads)
  });

  it("matches by option text when the value differs (Maharashtra -> MH)", async () => {
    document.body.innerHTML = SELECT2_HTML;
    const select = document.getElementById("state-select") as HTMLSelectElement;
    const action = {
      action_id: "a-2", run_id: "r", tab_id: 1, origin: "http://localhost:3000",
      sequence_number: 1, kind: "SELECT_OPTION",
      target: { field_id: "state-combo", role: "combobox", accessible_name: "State",
        input_type: "combobox", label: "State", name_attr: "state", autocomplete: null,
        placeholder: null, bounding_box: null },
      value_ref: null, resolved_value: "Maharashtra",
      expected_effect: { field_value: "Maharashtra", checked: null, selected_option: null,
        validation_error: false, dialog_dismissed: null, navigation_expected: null,
        expected_url_prefix: null },
      risk: "low", idempotency_key: "k2", source_observation_seq: null,
      approval_token_id: null, upload_file: null, method_hint: null,
    } as const;
    expect((await executeAction(document, action)).status).toBe("EXECUTED");
    expect(select.value).toBe("MH");  // matched by option text
  });
});
