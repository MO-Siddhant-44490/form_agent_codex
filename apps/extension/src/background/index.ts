// Background service worker: owns the tab session and enforces the command
// guards before anything reaches the page. Every action is validated against
// the contract schema, guard-checked, executed by the content script, then
// independently verified against a FRESH observation (invariants 6, 7, 8).
import {
  BrowserActionSchema,
  PageObservationSchema,
  type ActionResult,
  type ApprovalToken,
  type BrowserAction,
  type PageObservation,
} from "@form-agent/contracts";
import { checkAction, type GuardRecords, type GuardSession } from "../guards/session-guards";
import { verifyAction } from "../verification/verify";
import type {
  ExecuteOutcome,
  ExecuteRequest,
  ExecuteResponse,
  ObserveRequest,
  ObserveResponse,
  PanelCommand,
  SessionState,
} from "../shared/messages";

const state: SessionState = {
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

async function attachToTab(tabId: number, url: string): Promise<SessionState> {
  // Re-binding to a tab starts a fresh local session (plan.md §5.1).
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

async function observe(): Promise<SessionState> {
  if (!(await validateSessionOrigin()) || state.runId === null || state.tabId === null) {
    return state;
  }
  const request: ObserveRequest = {
    type: "FA_OBSERVE",
    runId: state.runId,
    tabId: state.tabId,
    observationSeq: ++observationSeq,
  };
  const response = (await chrome.tabs.sendMessage(state.tabId, request)) as ObserveResponse;
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

async function execute(rawAction: unknown): Promise<ExecuteOutcome> {
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
  const response = (await chrome.tabs.sendMessage(state.tabId, request)) as ExecuteResponse;
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

async function attachActiveTab(): Promise<SessionState> {
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

// Product flow: drive a fill in the active tab via the backend over a
// WebSocket. The backend does the mapping (Bedrock) and orchestration; this
// worker answers its observe/execute requests against the real tab and relays
// the result to the side panel.
async function fillViaBackend(facts: unknown[], backendUrl: string): Promise<void> {
  const fail = (error: string) =>
    chrome.runtime.sendMessage({ type: "FA_FILL_DONE", result: { type: "fill_error", error } });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) {
    fail("No active tab. Open the form in a tab first.");
    return;
  }
  if (!/^https?:/.test(tab.url)) {
    fail(`Cannot attach to ${tab.url} — open a normal web page with a form.`);
    return;
  }
  try {
    await attachToTab(tab.id, tab.url);
  } catch (e) {
    fail(`Could not attach to this tab: ${e instanceof Error ? e.message : String(e)}`);
    return;
  }

  // Create a backend run and align this session's run id with it (so the
  // driver's actions pass the run/tab/origin guards).
  let run_id: string;
  let session_token: string;
  try {
    const runResp = await fetch(`${backendUrl}/runs`, { method: "POST" });
    if (!runResp.ok) {
      fail(`Backend returned ${runResp.status}. Is it running at ${backendUrl}?`);
      return;
    }
    ({ run_id, session_token } = await runResp.json());
  } catch {
    fail(`Cannot reach the backend at ${backendUrl}. Start it: uv run python -m agent_backend.api.server`);
    return;
  }
  state.runId = run_id;

  const wsUrl = `${backendUrl.replace(/^http/, "ws")}/ws/${run_id}?token=${session_token}`;
  const ws = new WebSocket(wsUrl);
  let inboundSeq = 0;

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "hello", origin: state.origin, tab_id: state.tabId }));
    ws.send(JSON.stringify({ type: "start_fill", facts }));
    chrome.runtime.sendMessage({ type: "FA_FILL_STARTED" });
  };

  ws.onmessage = async (event) => {
    const env = JSON.parse(event.data as string);
    if (env.type === "fill_result" || env.type === "fill_error") {
      chrome.runtime.sendMessage({ type: "FA_FILL_DONE", result: env });
      ws.close();
      return;
    }
    const payload = env.payload ?? {};
    const reply: Record<string, unknown> = {
      protocol_version: env.protocol_version,
      message_type: "action_result",
      run_id,
      sent_at: new Date().toISOString(),
      payload: { _correlation_id: payload._correlation_id, _inbound_seq: ++inboundSeq } as Record<
        string,
        unknown
      >,
    };
    const rp = reply.payload as Record<string, unknown>;
    if (payload.command === "observe") {
      await observe();
      rp.observation = state.lastObservation;
    } else if (payload.command === "execute") {
      const outcome = await execute(payload.action);
      rp.result = outcome.result;
      if (outcome.state.lastObservation) rp.observation = outcome.state.lastObservation;
      if (outcome.verification) rp.verification = outcome.verification;
      // Report per-field progress to the panel as it fills.
      chrome.runtime.sendMessage({
        type: "FA_FILL_PROGRESS",
        field: outcome.result.action_id,
        status: outcome.result.status,
        verification: outcome.verification?.status ?? null,
      });
    }
    ws.send(JSON.stringify(reply));
  };

  ws.onerror = () => {
    chrome.runtime.sendMessage({
      type: "FA_FILL_DONE",
      result: { type: "fill_error", error: "backend connection failed (is it running?)" },
    });
  };
}

chrome.runtime.onMessage.addListener(
  (message: PanelCommand, _sender, sendResponse: (s: SessionState) => void) => {
    if (message?.type === "FA_ATTACH_ACTIVE_TAB") {
      attachActiveTab().then(sendResponse);
      return true;
    }
    if (message?.type === "FA_OBSERVE_NOW") {
      observe().then(sendResponse);
      return true;
    }
    if (message?.type === "FA_GET_STATE") {
      sendResponse(state);
      return false;
    }
    if (message?.type === "FA_FILL") {
      void fillViaBackend(message.facts ?? [], message.backendUrl ?? "http://127.0.0.1:8000");
      sendResponse(state);
      return false;
    }
    return false;
  },
);

chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => {});

// Test hooks: lets the Playwright harness drive the loop from the
// service-worker context, standing in for the side panel's user gesture and
// the (Slice 4) backend. Excluded from any store build.
declare global {
  // eslint-disable-next-line no-var
  var __formAgentTest: {
    attachToTab(tabId: number, url: string): Promise<SessionState>;
    observe(): Promise<SessionState>;
    execute(action: unknown): Promise<ExecuteOutcome>;
    getState(): SessionState;
    setFixtureMode(on: boolean): void;
    grantApproval(token: ApprovalToken): void;
  };
}
globalThis.__formAgentTest = {
  attachToTab,
  observe,
  execute,
  getState: () => state,
  setFixtureMode: (on: boolean) => {
    fixtureMode = on;
  },
  grantApproval: (token: ApprovalToken) => {
    records.approvals.set(token.token_id, token);
  },
};
