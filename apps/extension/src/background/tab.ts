// The tab session: attach to a tab, and observe/execute against it behind the
// deterministic command guards. Every action is validated against the contract
// schema, guard-checked (binding, replay, idempotency, submission lock),
// executed by the content script, then independently verified against a FRESH
// observation (invariants 6, 7, 8). No backend knowledge lives here.
import {
  BrowserActionSchema,
  PageObservationSchema,
  type ActionResult,
  type ApprovalToken,
  type BrowserAction,
  type PageObservation,
} from "@form-agent/contracts";
import { checkAction, type GuardRecords, type GuardSession } from "../guards/session-guards";
import type {
  ExecuteOutcome,
  ExecuteRequest,
  ExecuteResponse,
  ObserveRequest,
  ObserveResponse,
  SessionState,
} from "../shared/messages";
import { verifyAction } from "../verification/verify";

export const state: SessionState = {
  attached: false,
  runId: null,
  tabId: null,
  origin: null,
  lastObservation: null,
  error: null,
};
let observationSeq = 0;
let lastActionSeq = 0;
let fixtureMode = false;
const records: GuardRecords = {
  completed: new Map<string, ActionResult>(),
  approvals: new Map<string, ApprovalToken>(),
};

function newRunId(): string {
  return `local-${crypto.randomUUID()}`;
}

/** Bind to a tab and inject the content script. Starts a fresh local session. */
export async function attachToTab(tabId: number, url: string): Promise<SessionState> {
  const origin = new URL(url).origin;
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  state.attached = true;
  state.runId = newRunId();
  state.tabId = tabId;
  state.origin = origin;
  state.lastObservation = null;
  state.error = null;
  observationSeq = 0;
  lastActionSeq = 0;
  fixtureMode = false;
  records.completed.clear();
  records.approvals.clear();
  return state;
}

/** A full page load (a wizard's "Next" that POSTs, a redirect) replaces the
 * document, and the content script injected at attach goes with it. Messages
 * to the tab then fail with "Receiving end does not exist". Re-inject into the
 * new document and retry once. Only called after validateSessionOrigin(), so
 * the script is never injected into a different origin (invariant 8); the
 * content script guards against double injection itself. */
const NO_RECEIVER = /Receiving end does not exist|Could not establish connection/i;

async function sendToContent<T>(tabId: number, message: unknown): Promise<T> {
  try {
    return (await chrome.tabs.sendMessage(tabId, message)) as T;
  } catch (error) {
    if (!(error instanceof Error) || !NO_RECEIVER.test(error.message)) throw error;
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return (await chrome.tabs.sendMessage(tabId, message)) as T;
  }
}

/** Re-bind the guard state to a known tab/run without re-injecting a fresh run
 * id — used when reconnecting a backend session after the worker was recycled. */
export function restoreSession(runId: string, tabId: number, origin: string | null): void {
  state.attached = true;
  state.runId = runId;
  state.tabId = tabId;
  state.origin = origin;
  state.error = null;
}

/** Each backend fill/edit starts its own action sequence at 1; the guard's
 * replay counter must be reset so those actions aren't rejected as stale. */
export function resetActionSequence(): void {
  lastActionSeq = 0;
}

export function setFixtureMode(on: boolean): void {
  fixtureMode = on;
}

export function grantApproval(token: ApprovalToken): void {
  records.approvals.set(token.token_id, token);
}

async function validateSessionOrigin(): Promise<boolean> {
  // Origin re-validation before every observe/execute (invariant 8).
  if (!state.attached || state.tabId === null) {
    state.error = "not attached";
    return false;
  }
  const tab = await chrome.tabs.get(state.tabId);
  const currentOrigin = tab.url ? new URL(tab.url).origin : null;
  if (currentOrigin !== state.origin) {
    state.attached = false;
    state.error = `origin changed (${state.origin} -> ${currentOrigin}); re-attach required`;
    return false;
  }
  return true;
}

export async function observe(): Promise<SessionState> {
  if (!(await validateSessionOrigin()) || state.runId === null || state.tabId === null) {
    return state;
  }
  const request: ObserveRequest = {
    type: "FA_OBSERVE",
    runId: state.runId,
    tabId: state.tabId,
    observationSeq: ++observationSeq,
  };
  const response = await sendToContent<ObserveResponse>(state.tabId, request);
  if (!response.ok) {
    state.error = response.error;
    return state;
  }
  // Contract validation at the trust boundary.
  const parsed: PageObservation = PageObservationSchema.parse(response.observation);
  state.lastObservation = parsed;
  state.error = null;
  return state;
}

function rejected(action: BrowserAction, reason: string, detail: string): ActionResult {
  return {
    action_id: action.action_id,
    status: "REJECTED",
    rejection_reason: reason as ActionResult["rejection_reason"],
    error: detail,
    failure_class: null,
    executed_at: new Date().toISOString(),
  };
}

export async function execute(rawAction: unknown): Promise<ExecuteOutcome> {
  // 1. Contract validation: malformed actions never reach guard logic.
  const action = BrowserActionSchema.parse(rawAction);

  if (!(await validateSessionOrigin()) || state.tabId === null || state.runId === null) {
    return {
      result: rejected(action, "origin_mismatch", state.error ?? "not attached"),
      verification: null,
      state,
    };
  }

  // 2. Deterministic guards: binding, replay, idempotency, submission lock.
  const session: GuardSession = {
    runId: state.runId,
    tabId: state.tabId,
    origin: state.origin!,
    lastActionSeq,
    lastObservationSeq: state.lastObservation?.observation_seq ?? 0,
    fixtureMode,
  };
  const guardVerdict = checkAction(session, records, action);
  if (!guardVerdict.allowed) {
    return {
      result: rejected(action, guardVerdict.reason, guardVerdict.detail),
      verification: null,
      state,
    };
  }
  if (guardVerdict.duplicate) {
    // Duplicate delivery: return the recorded result, repeat nothing.
    return {
      result: { ...guardVerdict.duplicate, status: "DUPLICATE" },
      verification: null,
      state,
    };
  }

  // 3. Execute in the page.
  const request: ExecuteRequest = {
    type: "FA_EXECUTE",
    action,
    expectedFingerprint: state.lastObservation?.page_fingerprint ?? null,
  };
  const response = await sendToContent<ExecuteResponse>(state.tabId, request);
  if (!response.ok) {
    return { result: rejected(action, "unsupported", response.error), verification: null, state };
  }
  const result = response.result;

  if (result.status === "EXECUTED") {
    lastActionSeq = action.sequence_number;
    if (action.idempotency_key) records.completed.set(action.idempotency_key, result);
    if (action.kind === "SUBMIT" && action.approval_token_id) {
      // Single-use token: consumed on execution.
      const token = records.approvals.get(action.approval_token_id);
      if (token) records.approvals.set(token.token_id, { ...token, used: true });
    }
  }

  // 4. Independent verification against a fresh observation (invariant 7).
  let verification = null;
  if (result.status === "EXECUTED") {
    await observe();
    if (state.lastObservation) verification = verifyAction(action, state.lastObservation);
  }
  return { result, verification, state };
}

export async function attachActiveTab(): Promise<SessionState> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) {
    state.error = "no active tab";
    return state;
  }
  try {
    return await attachToTab(tab.id, tab.url);
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
    return state;
  }
}
