// Slice 5: perception and action across open shadow roots and same-origin
// iframes. Drives attach + observe + execute through the background test hook.
import { expect, test, type BrowserContext, type Page, type Worker } from "@playwright/test";
import type { ActionResult, PageObservation } from "@form-agent/contracts";
import { FIXTURES, attachAndObserve, launchWithExtension } from "./helpers";

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

type Outcome = {
  result: ActionResult;
  state: { runId: string | null; tabId: number | null; origin: string | null; lastObservation: PageObservation | null };
};

async function fill(fieldId: string, name: string, value: string, seq: number, obsSeq: number, state: Outcome["state"]) {
  const action = {
    action_id: `a-${seq}`, run_id: state.runId, tab_id: state.tabId, origin: state.origin,
    sequence_number: seq, kind: "SET_TEXT",
    target: { field_id: fieldId, role: "textbox", accessible_name: null, input_type: "text",
      label: null, name_attr: name, autocomplete: null, placeholder: null, bounding_box: null },
    value_ref: "fact://x", resolved_value: value,
    expected_effect: { field_value: value, checked: null, selected_option: null, validation_error: false,
      dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null },
    risk: "low", idempotency_key: `k-${fieldId}`, source_observation_seq: obsSeq, approval_token_id: null,
  };
  return serviceWorker.evaluate((a) => globalThis.__formAgentTest.execute(a) as Promise<Outcome>, action) as Promise<Outcome>;
}

test("discovers and fills fields inside an open shadow root", async () => {
  await page.goto(`${FIXTURES}/shadow-form/`);
  const state = await attachAndObserve(serviceWorker, page.url()) as Outcome["state"];
  const obs = state.lastObservation!;
  const ids = obs.fields!.map((f) => f.field_id);
  expect(ids).toEqual(["name", "email"]);  // fields inside the shadow root

  const o1 = await fill("name", "full_name", "Ada Lovelace", 1, obs.observation_seq, state);
  expect(o1.result.status).toBe("EXECUTED");
  const o2 = await fill("email", "email", "ada@example.test", 2, o1.state.lastObservation!.observation_seq, o1.state);
  expect(o2.result.status).toBe("EXECUTED");

  const values = await page.evaluate(() => {
    const form = document.querySelector("fa-form-card")!.shadowRoot!.getElementById("application-form") as HTMLFormElement;
    return Object.fromEntries(new FormData(form).entries());
  });
  expect(values).toEqual({ full_name: "Ada Lovelace", email: "ada@example.test" });
});

test("discovers and fills fields inside a same-origin iframe", async () => {
  await page.goto(`${FIXTURES}/iframe-form/`);
  const state = await attachAndObserve(serviceWorker, page.url()) as Outcome["state"];
  const obs = state.lastObservation!;
  expect(obs.fields!.map((f) => f.field_id)).toEqual(["name", "email"]);

  const o1 = await fill("name", "full_name", "Ada Lovelace", 1, obs.observation_seq, state);
  expect(o1.result.status).toBe("EXECUTED");
  const o2 = await fill("email", "email", "ada@example.test", 2, o1.state.lastObservation!.observation_seq, o1.state);
  expect(o2.result.status).toBe("EXECUTED");

  const values = await page.evaluate(() =>
    (window as never as { __fixture: { innerValues: Record<string, string> } }).__fixture.innerValues);
  expect(values).toEqual({ full_name: "Ada Lovelace", email: "ada@example.test" });
});
