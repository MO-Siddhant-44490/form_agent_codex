// Role-based perception + execution: div-built widgets are understood by
// their ARIA role/state, whatever framework built them. Two fixtures: one
// shaped like Google Forms, one a hand-rolled design-system form — the same
// code must handle both, with no site-specific branches.
import { describe, expect, it } from "vitest";
import type { BrowserAction } from "@form-agent/contracts";
import { executeAction } from "../../src/actions/execute";
import { discoverFieldsWithReport } from "../../src/perception/fields";
import { buildObservation } from "../../src/perception/page";
import { verifyAction } from "../../src/verification/verify";

const GOOGLE_FORM = `
<div role="list">
  <div role="listitem">
    <div role="heading" aria-level="3"><span id="q1">Full name</span><span id="q1r" aria-label="Required question">*</span></div>
    <input type="text" aria-labelledby="q1 q1r" aria-required="true" autocomplete="off">
  </div>
  <div role="listitem">
    <div role="heading" aria-level="3"><span id="q2">Date of birth</span></div>
    <input type="date" aria-labelledby="q2" aria-required="true">
  </div>
  <div role="listitem">
    <div role="heading" aria-level="3"><span id="q3">Gender</span></div>
    <div role="radiogroup" aria-labelledby="q3" aria-required="true">
      <label><div role="radio" aria-checked="false" aria-label="Male" data-value="Male" tabindex="0"></div><span>Male</span></label>
      <label><div role="radio" aria-checked="false" aria-label="Female" data-value="Female" tabindex="-1"></div><span>Female</span></label>
    </div>
  </div>
  <div role="listitem">
    <div role="heading" aria-level="3"><span id="q4">Marital status</span></div>
    <div role="listbox" aria-labelledby="q4" tabindex="0">
      <div role="option" aria-selected="true" data-value="" tabindex="0"><span>Choose</span></div>
      <div role="option" aria-selected="false" data-value="Single" style="display:none"><span>Single</span></div>
      <div role="option" aria-selected="false" data-value="Divorced" style="display:none"><span>Divorced</span></div>
    </div>
  </div>
  <div role="listitem">
    <div role="heading" aria-level="3"><span id="q5">Address</span></div>
    <textarea aria-labelledby="q5" rows="1"></textarea>
  </div>
  <div role="listitem">
    <div role="heading" aria-level="3"><span id="q6">Languages</span></div>
    <div role="list" aria-labelledby="q6">
      <div role="checkbox" aria-checked="false" aria-label="Tamil" data-answer-value="Tamil"></div>
      <div role="checkbox" aria-checked="false" aria-label="Hindi" data-answer-value="Hindi"></div>
    </div>
  </div>
</div>`;

// A different framework's markup: no Google idioms, same roles.
const DESIGN_SYSTEM_FORM = `
<form>
  <fieldset><legend>Preferred contact</legend>
    <span role="radio" aria-checked="true" id="c-email">Email</span>
    <span role="radio" aria-checked="false" id="c-phone">Phone</span>
  </fieldset>
  <label id="tos-l">I agree to the terms</label>
  <button type="button" role="switch" aria-checked="false" aria-labelledby="tos-l" id="tos"></button>
  <div class="form-group">
    <h4>Bio</h4>
    <div role="textbox" contenteditable="true" aria-multiline="true" id="bio"></div>
  </div>
  <div role="slider" aria-label="Satisfaction" aria-valuenow="3"></div>
</form>`;

function action(kind: BrowserAction["kind"], fieldId: string, target: BrowserAction["target"], value: string | null, checked: boolean | null = null): BrowserAction {
  return {
    action_id: `a-${fieldId}`,
    run_id: "local-test",
    tab_id: 1,
    origin: "http://localhost:3000",
    sequence_number: 1,
    source_observation_seq: 1,
    kind,
    target,
    resolved_value: value,
    value_ref: null,
    upload_file: null,
    expected_effect: { field_value: checked === null ? value : null, checked, selected_option: null, validation_error: null, expected_url_prefix: null, dialog_dismissed: null },
    risk: "low",
    idempotency_key: `k-${fieldId}`,
    method_hint: null,
    attempt: 0,
  } as unknown as BrowserAction;
}

describe("Google-Forms-shaped questions are perceived by role", () => {
  it("finds every question, in document order, with required-ness and options", () => {
    document.body.innerHTML = GOOGLE_FORM;
    const { fields, unrecognized } = discoverFieldsWithReport(document);
    expect(fields.map((f) => f.label ?? f.accessible_name)).toEqual([
      "Full name *",
      "Date of birth",
      "Gender",
      "Marital status",
      "Address",
      "Tamil",
      "Hindi",
    ]);
    expect(fields.map((f) => f.input_type)).toEqual(["text", "date", "radio", "combobox", "textarea", "checkbox", "checkbox"]);
    expect(fields[0]!.required).toBe(true); // aria-required, no HTML required
    expect(fields[2]!.required).toBe(true);
    expect(fields[2]!.options).toEqual(["Male", "Female"]);
    expect(fields[3]!.options).toEqual(["", "Single", "Divorced"]);
    expect(fields[3]!.current_value).toBeNull(); // "Choose" is a placeholder
    expect(fields[5]!.nearby_text).toBe("Languages");
    expect(unrecognized).toEqual([]);
  });

  it("selects a radio, picks a listbox option, ticks a checkbox — and the fresh observation verifies each", async () => {
    document.body.innerHTML = GOOGLE_FORM;
    const before = await buildObservation(document, { runId: "local-test", tabId: 1, observationSeq: 1, domStable: true });
    const gender = before.fields!.find((f) => f.label === "Gender")!;
    const marital = before.fields!.find((f) => f.label === "Marital status")!;
    const tamil = before.fields!.find((f) => f.label === "Tamil")!;

    const a1 = action("SET_RADIO", gender.field_id, gender.target, "Female");
    expect((await executeAction(document, a1)).status).toBe("EXECUTED");
    const a2 = action("SELECT_OPTION", marital.field_id, marital.target, "Divorced");
    const r2 = await executeAction(document, a2);
    expect(r2.error ?? r2.status).toBe("EXECUTED");
    const a3 = action("SET_CHECKBOX", tamil.field_id, tamil.target, null, true);
    const r3 = await executeAction(document, a3);
    expect(r3.error ?? r3.status).toBe("EXECUTED");

    // Simulate the site's own handlers reacting to the clicks (a real site
    // flips aria state itself; jsdom does not).
    document.querySelector("[data-value='Female']")!.setAttribute("aria-checked", "true");
    document.querySelectorAll("[role='option']").forEach((o) => o.setAttribute("aria-selected", String(o.getAttribute("data-value") === "Divorced")));
    document.querySelector("[data-answer-value='Tamil']")!.setAttribute("aria-checked", "true");

    const after = await buildObservation(document, { runId: "local-test", tabId: 1, observationSeq: 2, domStable: true });
    expect(verifyAction(a1, after).status).toBe("SUCCESS");
    expect(verifyAction(a2, after).status).toBe("SUCCESS");
    expect(verifyAction(a3, after).status).toBe("SUCCESS");
    expect(after.fields!.find((f) => f.label === "Gender")!.current_value).toBe("Female");
  });

  it("the site's click handlers actually receive the clicks", async () => {
    document.body.innerHTML = GOOGLE_FORM;
    const female = document.querySelector<HTMLElement>("[data-value='Female']")!;
    female.addEventListener("click", () => female.setAttribute("aria-checked", "true"));
    const { fields } = discoverFieldsWithReport(document);
    const gender = fields.find((f) => f.label === "Gender")!;
    await executeAction(document, action("SET_RADIO", gender.field_id, gender.target, "female"));
    expect(female.getAttribute("aria-checked")).toBe("true"); // case-insensitive match
  });
});

describe("the same code handles a different framework's ARIA form", () => {
  it("loose radios, a switch, a contenteditable textbox; a slider is reported, not skipped silently", async () => {
    document.body.innerHTML = DESIGN_SYSTEM_FORM;
    const { fields, unrecognized } = discoverFieldsWithReport(document);
    const byLabel = Object.fromEntries(fields.map((f) => [f.label, f]));
    expect(byLabel["Preferred contact"]!.input_type).toBe("radio");
    expect(byLabel["Preferred contact"]!.current_value).toBe("Email");
    expect(byLabel["I agree to the terms"]!.input_type).toBe("checkbox");
    expect(byLabel["I agree to the terms"]!.purpose).toBe("consent");
    expect(byLabel["Bio"]!.input_type).toBe("textarea");
    expect(unrecognized).toEqual([{ role: "slider", name: "Satisfaction" }]);

    const bio = byLabel["Bio"]!;
    await executeAction(document, action("SET_TEXT", bio.field_id, bio.target, "Designer in Chennai"));
    expect(document.getElementById("bio")!.textContent).toBe("Designer in Chennai");
    const phone = byLabel["Preferred contact"]!;
    const r = await executeAction(document, action("SET_RADIO", phone.field_id, phone.target, "Phone"));
    expect(r.status).toBe("EXECUTED");
  });
});
