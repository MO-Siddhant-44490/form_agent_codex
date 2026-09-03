// Modules 3-4 acceptance (Slice 1 closed loop): hard-coded facts fill the
// controlled form one typed action at a time, every effect independently
// verified, with the submission lock enforced throughout.
import { expect, test, type BrowserContext, type Page, type Worker } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ActionResult, BrowserAction, PageObservation, VerificationResult } from "@form-agent/contracts";
import { FIXTURES, attachAndObserve, launchWithExtension } from "./helpers";

const fixturesDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "fixtures");
const gt = JSON.parse(readFileSync(join(fixturesDir, "basic-form", "ground-truth.json"), "utf8"));

let context: BrowserContext;
let serviceWorker: Worker;
let page: Page;
let actionSeq = 0;

test.beforeAll(async () => {
  ({ context, serviceWorker } = await launchWithExtension());
  page = await context.newPage();
});
test.afterAll(async () => {
  await context.close();
});

type Outcome = {
  result: ActionResult;
  verification: VerificationResult | null;
  state: { runId: string | null; tabId: number | null; origin: string | null; lastObservation: PageObservation | null };
};

async function execute(action: BrowserAction): Promise<Outcome> {
  return serviceWorker.evaluate(
    (a) => globalThis.__formAgentTest.execute(a) as Promise<Outcome>,
    action,
  ) as Promise<Outcome>;
}

function sessionInfo(state: Outcome["state"]): { runId: string; tabId: number; origin: string; obsSeq: number } {
  return {
    runId: state.runId!,
    tabId: state.tabId!,
    origin: state.origin!,
    obsSeq: state.lastObservation!.observation_seq,
  };
}

const KIND_FOR_TYPE: Record<string, BrowserAction["kind"]> = {
  text: "SET_TEXT", email: "SET_TEXT", tel: "SET_TEXT", date: "SET_DATE",
  number: "SET_NUMBER", "select-one": "SELECT_OPTION", radio: "SET_RADIO", checkbox: "SET_CHECKBOX",
};

function buildAction(
  gtField: (typeof gt.fields)[number],
  session: { runId: string; tabId: number; origin: string; obsSeq: number },
  overrides: Partial<BrowserAction> = {},
): BrowserAction {
  const kind = KIND_FOR_TYPE[gtField.input_type]!;
  const isCheckbox = kind === "SET_CHECKBOX";
  const fieldId = gtField.input_type === "radio" ? `radio-group:${gtField.name}` : gtField.dom_id;
  const value: string | null = isCheckbox ? null : gtField.expected_value;
  return {
    action_id: `a-${++actionSeq}`,
    run_id: session.runId,
    tab_id: session.tabId,
    origin: session.origin,
    sequence_number: actionSeq,
    kind,
    target: {
      field_id: fieldId, role: gtField.input_type === "radio" ? "radiogroup" : "textbox",
      accessible_name: gtField.label ?? null, input_type: gtField.input_type,
      label: gtField.label ?? null, name_attr: gtField.name, autocomplete: null,
      placeholder: null, bounding_box: null,
    },
    value_ref: `fact://${gtField.fact_key}`,
    resolved_value: value,
    expected_effect: {
      field_value: isCheckbox ? null : gtField.expected_value,
      checked: isCheckbox ? gtField.expected_checked : null,
      selected_option: null,
      validation_error: false,
      dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
    },
    risk: "low",
    idempotency_key: `${session.runId}:${fieldId}:${isCheckbox ? gtField.expected_checked : gtField.expected_value}`,
    source_observation_seq: session.obsSeq,
    approval_token_id: null,
    ...overrides,
  };
}

test("fills the entire basic form from hard-coded facts, verifying every action", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  let state = (await attachAndObserve(serviceWorker, page.url())) as Outcome["state"];
  actionSeq = 0;

  for (const gtField of gt.fields) {
    const action = buildAction(gtField, sessionInfo(state));
    const outcome = await execute(action);
    expect(outcome.result.status, `${gtField.dom_id} execution`).toBe("EXECUTED");
    expect(outcome.verification?.status, `${gtField.dom_id} verification`).toBe("SUCCESS");
    state = outcome.state;
  }

  // The page itself confirms every value (ground truth, not our observation).
  const domValues = await page.evaluate(() => {
    const form = document.getElementById("application-form") as HTMLFormElement;
    const data = Object.fromEntries(new FormData(form).entries());
    data.subscribe = String((form.elements.namedItem("subscribe") as HTMLInputElement).checked);
    return data;
  });
  expect(domValues).toMatchObject({
    full_name: "Ada Lovelace",
    email: "ada@example.test",
    phone: "+1 555 010 2030",
    date_of_birth: "1998-04-17",
    country: "IN",
    years_experience: "5",
    contact_method: "email",
    subscribe: "true",
  });
  // Honeypot untouched.
  expect(await page.evaluate(() => (document.getElementById("website") as HTMLInputElement).value)).toBe("");
  // Nothing was submitted (invariant 1: stop at final review).
  expect(await page.evaluate(() => (window as never as { __fixture: { submissions: unknown[] } }).__fixture.submissions.length)).toBe(0);
});

test("duplicate delivery returns DUPLICATE and repeats nothing", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  const state = (await attachAndObserve(serviceWorker, page.url())) as Outcome["state"];
  actionSeq = 0;
  const action = buildAction(gt.fields[0], sessionInfo(state));

  const first = await execute(action);
  expect(first.result.status).toBe("EXECUTED");

  const redelivered = await execute({ ...action });
  expect(redelivered.result.status).toBe("DUPLICATE");
});

test("wrong-origin, stale-sequence, and stale-observation commands are rejected", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  const state = (await attachAndObserve(serviceWorker, page.url())) as Outcome["state"];
  actionSeq = 0;
  const session = sessionInfo(state);

  const wrongOrigin = buildAction(gt.fields[0], session, { origin: "https://evil.test" });
  expect((await execute(wrongOrigin)).result).toMatchObject({
    status: "REJECTED", rejection_reason: "origin_mismatch",
  });

  const good = buildAction(gt.fields[0], session);
  expect((await execute(good)).result.status).toBe("EXECUTED");

  const staleSeq = buildAction(gt.fields[1], session, {
    sequence_number: good.sequence_number, // replay of an executed slot
    idempotency_key: "different-key",
  });
  expect((await execute(staleSeq)).result).toMatchObject({
    status: "REJECTED", rejection_reason: "stale_sequence",
  });

  const staleObs = buildAction(gt.fields[1], session, {
    sequence_number: 99,
    idempotency_key: "another-key",
    source_observation_seq: session.obsSeq, // stale: verification re-observed since
  });
  expect((await execute(staleObs)).result).toMatchObject({
    status: "REJECTED", rejection_reason: "stale_observation",
  });
});

test("verification never reports success for a wrong value or validation error", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  const state = (await attachAndObserve(serviceWorker, page.url())) as Outcome["state"];
  actionSeq = 0;
  const session = sessionInfo(state);

  // Lie in the expected effect: executor types X, expectation says Y.
  const emailGt = gt.fields.find((f: { dom_id: string }) => f.dom_id === "email")!;
  const lying = buildAction(emailGt, session, {
    resolved_value: "ada@example.test",
    expected_effect: {
      field_value: "different@example.test", checked: null, selected_option: null,
      validation_error: false, dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
    },
  });
  const outcome = await execute(lying);
  expect(outcome.result.status).toBe("EXECUTED");
  expect(outcome.verification).toMatchObject({ status: "RETRYABLE_FAILURE", failure_class: "value_mismatch" });

  // Invalid email: the field-level validation error must fail verification.
  const invalid = buildAction(emailGt, sessionInfo(outcome.state as Outcome["state"]), {
    sequence_number: 50,
    idempotency_key: "invalid-email-attempt",
    resolved_value: "not-an-email",
    expected_effect: {
      field_value: "not-an-email", checked: null, selected_option: null,
      validation_error: false, dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
    },
  });
  const invalidOutcome = await execute(invalid);
  expect(invalidOutcome.result.status).toBe("EXECUTED");
  expect(invalidOutcome.verification).toMatchObject({
    status: "RETRYABLE_FAILURE",
    failure_class: "validation_error",
  });
});

test("SUBMIT is locked without approval and single-use with it", async () => {
  await page.goto(`${FIXTURES}/basic-form/`);
  const state = (await attachAndObserve(serviceWorker, page.url())) as Outcome["state"];
  actionSeq = 0;
  let session = sessionInfo(state);

  // Fill the form first so submission is valid.
  for (const gtField of gt.fields) {
    const outcome = await execute(buildAction(gtField, session));
    expect(outcome.verification?.status).toBe("SUCCESS");
    session = sessionInfo(outcome.state as Outcome["state"]);
  }

  const submitAction = (tokenId: string | null, seq: number): BrowserAction => ({
    action_id: `submit-${seq}`, run_id: session.runId, tab_id: session.tabId,
    origin: session.origin, sequence_number: seq, kind: "SUBMIT",
    target: {
      field_id: "submit-btn", role: "button", accessible_name: "Submit application",
      input_type: "submit", label: null, name_attr: null, autocomplete: null,
      placeholder: null, bounding_box: null,
    },
    value_ref: null, resolved_value: null,
    expected_effect: {
      field_value: null, checked: null, selected_option: null, validation_error: null,
      dialog_dismissed: null, navigation_expected: null,
      expected_url_prefix: `${FIXTURES}/basic-form/`,
    },
    risk: "high", idempotency_key: `${session.runId}:submit:${seq}`,
    source_observation_seq: session.obsSeq, approval_token_id: tokenId,
  });

  // 1. No token -> schema-level rejection is bypassed by passing token later;
  //    guard-level: unknown token id.
  const noToken = await execute(submitAction("tok-unknown", 100));
  expect(noToken.result).toMatchObject({ status: "REJECTED", rejection_reason: "missing_approval" });
  expect(await page.evaluate(() => (window as never as { __fixture: { submissions: unknown[] } }).__fixture.submissions.length)).toBe(0);

  // 2. Grant a valid scoped token -> SUBMIT executes; fixture records locally.
  await serviceWorker.evaluate((args) => {
    globalThis.__formAgentTest.grantApproval({
      token_id: "tok-e2e", run_id: args.runId, origin: args.origin, scope: "submit",
      issued_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      used: false,
    });
  }, { runId: session.runId, origin: session.origin });

  const approved = await execute(submitAction("tok-e2e", 101));
  expect(approved.result.status).toBe("EXECUTED");
  expect(approved.verification?.status).toBe("SUCCESS");
  const submissions = await page.evaluate(
    () => (window as never as { __fixture: { submissions: { botFlagged: boolean }[] } }).__fixture.submissions,
  );
  expect(submissions).toHaveLength(1);
  expect(submissions[0]!.botFlagged).toBe(false);

  // 3. The token is single-use: a second SUBMIT with it is rejected.
  // (Use the post-submit observation seq so the stale-observation guard does
  // not fire first — this test is about the token.)
  session = sessionInfo(approved.state as Outcome["state"]);
  const reuse = await execute(submitAction("tok-e2e", 102));
  expect(reuse.result).toMatchObject({ status: "REJECTED", rejection_reason: "missing_approval" });
  expect(await page.evaluate(() => (window as never as { __fixture: { submissions: unknown[] } }).__fixture.submissions.length)).toBe(1);
});
