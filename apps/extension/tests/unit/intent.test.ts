// Chat intent parsing (Phase 1): correct/add a value, or re-fill.
import { describe, expect, it } from "vitest";
import { parseIntent, slugKey } from "../../src/background/intent";

describe("parseIntent", () => {
  it("recognizes refill commands", () => {
    for (const t of ["refill", "re-fill", "fill again", "fill it again", "try again", "retry", "GO"]) {
      expect(parseIntent(t).kind).toBe("refill");
    }
  });

  it("parses 'set X to Y' into a fact update", () => {
    expect(parseIntent("set state to Karnataka")).toEqual({
      kind: "set",
      key: "state",
      value: "Karnataka",
    });
  });

  it("handles change/update/make and =/: separators", () => {
    expect(parseIntent("change district to Pune")).toEqual({ kind: "set", key: "district", value: "Pune" });
    expect(parseIntent("update full name to Asha Rao")).toEqual({
      kind: "set",
      key: "full_name",
      value: "Asha Rao",
    });
    expect(parseIntent("email: a@b.com")).toEqual({ kind: "set", key: "email", value: "a@b.com" });
    expect(parseIntent("pincode = 560001")).toEqual({ kind: "set", key: "pincode", value: "560001" });
  });

  it("does not swallow a value that contains a colon", () => {
    expect(parseIntent("set link to http://x.test")).toEqual({
      kind: "set",
      key: "link",
      value: "http://x.test",
    });
  });

  it("returns unknown for free chatter", () => {
    expect(parseIntent("what did you fill?").kind).toBe("unknown");
    expect(parseIntent("hello there").kind).toBe("unknown");
  });

  it("slugKey normalizes a field name to a fact key", () => {
    expect(slugKey("Full Name")).toBe("full_name");
    expect(slugKey("  Date of Birth ")).toBe("date_of_birth");
  });
});
