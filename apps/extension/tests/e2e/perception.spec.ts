// Module 2 acceptance: the extension discovers all actionable fixture fields
// without an LLM and without modifying the page; honeypots are excluded;
// credential values never leave the page.
import { expect, test, type BrowserContext, type Page, type Worker } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { FIXTURES, attachAndObserve, launchWithExtension, observeAgain } from "./helpers";

const fixturesDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "fixtures");
const groundTruth = (name: string) =>
  JSON.parse(readFileSync(join(fixturesDir, name, "ground-truth.json"), "utf8"));

let context: BrowserContext;
let serviceWorker: Worker;
let page: Page;

test.beforeAll(async () => {
  ({ context, serviceWorker } = await launchWithExtension());
  page = await context.newPage();
});
test.afterAll(async () => {
  await context.close();
});

test("discovers all basic-form fields per ground truth", async () => {
  const gt = groundTruth("basic-form");
  await page.goto(`${FIXTURES}${gt.url_path}`);
  const state = await attachAndObserve(serviceWorker, page.url());

  expect(state.error).toBeNull();
  const obs = state.lastObservation!;
  expect(obs.fields!.length).toBe(gt.fields.length);

  for (const expected of gt.fields) {
    const field =
      expected.input_type === "radio"
        ? obs.fields!.find((f) => f.input_type === "radio" && f.target.name_attr === expected.name)
        : obs.fields!.find((f) => f.field_id === expected.dom_id);
    expect(field, `missing field ${expected.dom_id}`).toBeTruthy();
    expect(field!.input_type).toBe(expected.input_type);
    expect(field!.required).toBe(expected.required);
    if (expected.label && expected.input_type !== "radio") {
      expect(field!.label).toBe(expected.label);
    }
    if (expected.options) expect(field!.options).toEqual(expected.options);
    if (expected.input_type === "radio") {
      expect(field!.options).toContain(expected.expected_value);
    }
  }

  expect(obs.login_detected).toBe(false);
  expect(obs.captcha_detected).toBe(false);
  expect(obs.classification![0]!.label).toBe("form");
});

test("honeypot is never discovered as actionable", async () => {
  const gt = groundTruth("basic-form");
  await page.goto(`${FIXTURES}${gt.url_path}`);
  const state = await attachAndObserve(serviceWorker, page.url());
  const ids = state.lastObservation!.fields!.map((f) => f.field_id);
  const names = state.lastObservation!.fields!.map((f) => f.target.name_attr);
  for (const hp of gt.honeypots) {
    expect(ids).not.toContain(hp.dom_id);
    expect(names).not.toContain(hp.name);
  }
});

test("observation does not modify the page", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  const before = await page.evaluate(() => document.documentElement.outerHTML);
  await attachAndObserve(serviceWorker, page.url());
  const after = await page.evaluate(() => document.documentElement.outerHTML);
  expect(after).toBe(before);
});

test("re-perception sees dynamically added fields and bumps sequence", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  const first = await attachAndObserve(serviceWorker, page.url());
  const seq1 = first.lastObservation!.observation_seq;

  await page.evaluate(() => {
    const label = document.createElement("label");
    label.htmlFor = "middle-name";
    label.textContent = "Middle name";
    const input = document.createElement("input");
    input.id = "middle-name";
    input.type = "text";
    document.getElementById("application-form")!.append(label, input);
  });

  const second = await observeAgain(serviceWorker);
  expect(second.lastObservation!.observation_seq).toBeGreaterThan(seq1);
  const added = second.lastObservation!.fields!.find((f) => f.field_id === "middle-name");
  expect(added).toMatchObject({ label: "Middle name", input_type: "text" });
  expect(second.lastObservation!.page_fingerprint).not.toBe(first.lastObservation!.page_fingerprint);
});

test("login fixture: classification flags set, password never observed", async () => {
  const gt = groundTruth("login-form");
  await page.goto(`${FIXTURES}${gt.url_path}`);
  const state = await attachAndObserve(serviceWorker, page.url());
  const obs = state.lastObservation!;

  expect(obs.login_detected).toBe(gt.expectations.login_detected);
  expect(obs.captcha_detected).toBe(gt.expectations.captcha_detected);
  expect(obs.classification![0]!.label).toBe("login");

  const pw = obs.fields!.find((f) => f.field_id === gt.expectations.password_field.dom_id)!;
  expect(pw.value_redacted).toBe(true);
  expect(pw.current_value).toBeNull();
  // The secret must not appear anywhere in the serialized observation.
  const secret = gt.expectations.password_field.secret_value_that_must_never_appear;
  expect(JSON.stringify(obs)).not.toContain(secret);
});

test("cross-origin navigation drops the session and requires re-attach", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  await attachAndObserve(serviceWorker, page.url());
  // localhost vs 127.0.0.1 is a different origin for the same server.
  await page.goto("http://localhost:4173/basic-form/");
  const state = await observeAgain(serviceWorker);
  expect(state.attached).toBe(false);
  expect(state.error).toContain("origin changed");
});
