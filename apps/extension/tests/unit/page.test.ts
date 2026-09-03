import { describe, expect, it } from "vitest";
import { PageObservationSchema } from "@form-agent/contracts";
import { buildObservation, detectCaptcha, detectLogin } from "../../src/perception/page";

const ctx = { runId: "local-test", tabId: 1, observationSeq: 1, domStable: true };

describe("page classification signals", () => {
  it("detects visible password fields as login", () => {
    document.body.innerHTML = `<input type="password">`;
    expect(detectLogin(document)).toBe(true);
  });

  it("ignores hidden password fields", () => {
    document.body.innerHTML = `<input type="password" style="display:none">`;
    expect(detectLogin(document)).toBe(false);
  });

  it("detects captcha placeholders", () => {
    document.body.innerHTML = `<div class="g-recaptcha" data-sitekey="k"></div>`;
    expect(detectCaptcha(document)).toBe(true);
  });
});

describe("buildObservation", () => {
  it("produces a schema-valid observation without mutating the page", async () => {
    document.body.innerHTML = `<label for="a">Name</label><input id="a" type="text">`;
    const before = document.body.innerHTML;
    const obs = await buildObservation(document, ctx);
    expect(document.body.innerHTML).toBe(before);
    // jsdom origin is http://localhost:3000 by default — matches the contract.
    PageObservationSchema.parse(JSON.parse(JSON.stringify(obs)));
    expect(obs.fields).toHaveLength(1);
    expect(obs.page_fingerprint).toMatch(/^sha256:/);
  });

  it("fingerprint is stable across identical states and changes with structure", async () => {
    document.body.innerHTML = `<input id="a" type="text">`;
    const one = await buildObservation(document, ctx);
    const two = await buildObservation(document, ctx);
    expect(one.page_fingerprint).toBe(two.page_fingerprint);

    document.body.innerHTML = `<input id="a" type="text"><input id="b" type="email">`;
    const three = await buildObservation(document, ctx);
    expect(three.page_fingerprint).not.toBe(one.page_fingerprint);
  });

  it("fingerprint does not depend on field values (no PII in fingerprints)", async () => {
    document.body.innerHTML = `<input id="a" type="text" value="">`;
    const empty = await buildObservation(document, ctx);
    (document.getElementById("a") as HTMLInputElement).value = "Ada Lovelace";
    const filled = await buildObservation(document, ctx);
    expect(filled.page_fingerprint).toBe(empty.page_fingerprint);
  });
});
