import { describe, expect, it } from "vitest";
import { detectDatePickers, findDateCell, parseCellDate } from "../../src/perception/widgets";
import { discoverFields } from "../../src/perception/fields";

const DP_HTML = `
  <label for="dob">Date of birth</label>
  <div class="datepicker" data-datepicker>
    <input id="dob" role="combobox" aria-haspopup="grid" aria-controls="cal"
           data-value-input="date_of_birth" readonly>
    <input type="hidden" name="date_of_birth" value="1998-04-17">
    <div id="cal" role="grid">
      <button role="gridcell" data-date="1998-04-16">16</button>
      <button role="gridcell" data-date="1998-04-17">17</button>
    </div>
  </div>`;

describe("date picker detection", () => {
  it("detects the picker, its grid, and current value", () => {
    document.body.innerHTML = DP_HTML;
    const pickers = detectDatePickers(document);
    expect(pickers).toHaveLength(1);
    expect(pickers[0]!.currentValue).toBe("1998-04-17");
    expect(pickers[0]!.grid?.id).toBe("cal");
  });

  it("surfaces the picker as a date field routed to the datepicker executor", () => {
    document.body.innerHTML = DP_HTML;
    const field = discoverFields(document).find((f) => f.field_id === "dob")!;
    expect(field.input_type).toBe("date");
    expect(field.target.role).toBe("datepicker");
    expect(field.target.name_attr).toBe("date_of_birth");
  });

  it("findDateCell matches by data-date and by aria-label", () => {
    document.body.innerHTML = DP_HTML;
    const grid = document.getElementById("cal")!;
    expect(findDateCell(grid, "1998-04-17")!.textContent).toBe("17");
    expect(findDateCell(grid, "1998-01-01")).toBeNull();
  });

  it("parseCellDate understands common label formats", () => {
    expect(parseCellDate("April 17, 1998")).toBe("1998-04-17");
    expect(parseCellDate("17 Apr 1998")).toBe("1998-04-17");
    expect(parseCellDate("not a date")).toBeNull();
  });
});

import { findMonthNav, shownMonth } from "../../src/perception/widgets";

describe("date picker month navigation helpers", () => {
  it("infers the shown month from the mode of cell dates", () => {
    document.body.innerHTML = `
      <div id="cal" role="grid">
        <button role="gridcell" data-date="1998-03-31">31</button>
        <button role="gridcell" data-date="1998-04-01">1</button>
        <button role="gridcell" data-date="1998-04-15">15</button>
        <button role="gridcell" data-date="1998-04-30">30</button>
      </div>`;
    expect(shownMonth(document.getElementById("cal")!)).toEqual({ year: 1998, month: 4 });
  });

  it("finds previous/next month buttons by aria-label", () => {
    document.body.innerHTML = `
      <div class="datepicker" data-datepicker>
        <input id="dob" role="combobox">
        <button id="p" aria-label="Previous month">‹</button>
        <button id="n" aria-label="Next month">›</button>
      </div>`;
    const nav = findMonthNav(document.getElementById("dob")!);
    expect(nav.prev?.id).toBe("p");
    expect(nav.next?.id).toBe("n");
  });
});
