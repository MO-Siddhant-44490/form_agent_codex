// Negative security tests for the command guards (invariants 1, 6, 8).
import { describe, expect, it } from "vitest";
import type { ActionResult, ApprovalToken, BrowserAction } from "@form-agent/contracts";
import { checkAction, type GuardRecords, type GuardSession } from "../../src/guards/session-guards";

const session: GuardSession = {
  runId: "run-1",
  tabId: 7,
  origin: "http://127.0.0.1:4173",
  lastActionSeq: 5,
  lastObservationSeq: 3,
  fixtureMode: false,
};

function freshRecords(): GuardRecords {
  return { completed: new Map(), approvals: new Map() };
}

function action(overrides: Partial<BrowserAction> = {}): BrowserAction {
  return {
    action_id: "a-1",
    run_id: "run-1",
    tab_id: 7,
    origin: "http://127.0.0.1:4173",
    sequence_number: 6,
    kind: "SET_TEXT",
    target: {
      field_id: "full-name", role: "textbox", accessible_name: null, input_type: "text",
      label: null, name_attr: null, autocomplete: null, placeholder: null, bounding_box: null,
    },
    value_ref: "fact://f-1",
    resolved_value: "Ada",
    expected_effect: {
      field_value: "Ada", checked: null, selected_option: null, validation_error: false,
      dialog_dismissed: null, navigation_expected: null, expected_url_prefix: null,
    },
    risk: "low",
    idempotency_key: "run-1:full-name:Ada",
    source_observation_seq: 3,
    approval_token_id: null,
    ...overrides,
  };
}

function token(overrides: Partial<ApprovalToken> = {}): ApprovalToken {
  const now = Date.now();
  return {
    token_id: "tok-1",
    run_id: "run-1",
    origin: "http://127.0.0.1:4173",
    scope: "submit",
    issued_at: new Date(now).toISOString(),
    expires_at: new Date(now + 60_000).toISOString(),
    used: false,
    ...overrides,
  };
}

describe("binding and replay guards (invariant 8)", () => {
  it("allows a well-bound action", () => {
    expect(checkAction(session, freshRecords(), action())).toEqual({ allowed: true });
  });

  it.each([
    ["run_mismatch", { run_id: "run-2" }],
    ["tab_mismatch", { tab_id: 8 }],
    ["origin_mismatch", { origin: "https://evil.test" }],
    ["stale_sequence", { sequence_number: 5 }],
    ["stale_observation", { source_observation_seq: 2 }],
  ] as const)("rejects %s", (reason, overrides) => {
    const verdict = checkAction(session, freshRecords(), action(overrides));
    expect(verdict).toMatchObject({ allowed: false, reason });
  });
});

describe("idempotency (invariant 6)", () => {
  it("returns the recorded result for a duplicate key without re-execution", () => {
    const records = freshRecords();
    const prior: ActionResult = {
      action_id: "a-0", status: "EXECUTED", rejection_reason: null, error: null,
      executed_at: new Date().toISOString(),
    };
    records.completed.set("run-1:full-name:Ada", prior);
    const verdict = checkAction(session, records, action({ sequence_number: 9 }));
    expect(verdict).toEqual({ allowed: true, duplicate: prior });
  });
});

describe("submission lock (invariant 1)", () => {
  const submit = (overrides: Partial<BrowserAction> = {}) =>
    action({
      kind: "SUBMIT",
      approval_token_id: "tok-1",
      idempotency_key: "run-1:submit",
      expected_effect: {
        field_value: null, checked: null, selected_option: null, validation_error: null,
        dialog_dismissed: null, navigation_expected: true, expected_url_prefix: null,
      },
      ...overrides,
    });

  it("rejects SUBMIT without any token", () => {
    expect(checkAction(session, freshRecords(), submit({ approval_token_id: null })))
      .toMatchObject({ allowed: false, reason: "missing_approval" });
  });

  it("rejects SUBMIT with an unknown token", () => {
    expect(checkAction(session, freshRecords(), submit())).toMatchObject({
      allowed: false, reason: "missing_approval",
    });
  });

  it("rejects a used token (single-use)", () => {
    const records = freshRecords();
    records.approvals.set("tok-1", token({ used: true }));
    expect(checkAction(session, records, submit())).toMatchObject({
      allowed: false, reason: "missing_approval",
    });
  });

  it("rejects an expired token", () => {
    const records = freshRecords();
    records.approvals.set("tok-1", token({ expires_at: new Date(Date.now() - 1000).toISOString() }));
    expect(checkAction(session, records, submit())).toMatchObject({
      allowed: false, reason: "missing_approval",
    });
  });

  it("rejects a token bound to another origin", () => {
    const records = freshRecords();
    records.approvals.set("tok-1", token({ origin: "https://other.test" }));
    expect(checkAction(session, records, submit())).toMatchObject({
      allowed: false, reason: "missing_approval",
    });
  });

  it("allows SUBMIT with a valid scoped token", () => {
    const records = freshRecords();
    records.approvals.set("tok-1", token());
    expect(checkAction(session, records, submit())).toEqual({ allowed: true });
  });

  it("fixture mode permits SUBMIT without a token (controlled fixtures only)", () => {
    const fixtureSession = { ...session, fixtureMode: true };
    expect(checkAction(fixtureSession, freshRecords(), submit({ approval_token_id: null })))
      .toEqual({ allowed: true });
  });
});
