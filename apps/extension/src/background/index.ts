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
  ChatFact,
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

// Public value keys are non-sensitive geography; everything else is personal.
const PUBLIC_KEYS = new Set(["country", "state", "district", "locality", "pincode", "gender"]);

// A persistent conversational session. The WebSocket lives in this MV3 service
// worker, which Chrome terminates when idle — so the essentials are mirrored to
// chrome.storage.session and the socket is transparently RE-ESTABLISHED on the
// next command (see ensureConnection). Facts are the source of truth here.
type ChatSession = {
  ws: WebSocket;
  runId: string;
  token: string;
  backendUrl: string;
  facts: ChatFact[];
  busy: boolean;
};
let chat: ChatSession | null = null;

// A short trail of recent value changes so the agent can honor "change it back
// to the previous value". Sent to the interpreter with each chat turn.
type ValueChange = { key: string; from: string | null; to: string };
let recentChanges: ValueChange[] = [];

type SavedSession = {
  runId: string;
  token: string;
  backendUrl: string;
  tabId: number | null;
  origin: string | null;
  facts: ChatFact[];
  history: ValueChange[];
};

function agentMsg(text: string): void {
  chrome.runtime.sendMessage({ type: "FA_AGENT_MSG", text });
}

function saveSession(): void {
  if (!chat) return;
  const saved: SavedSession = {
    runId: chat.runId,
    token: chat.token,
    backendUrl: chat.backendUrl,
    tabId: state.tabId,
    origin: state.origin,
    facts: chat.facts,
    history: recentChanges,
  };
  void chrome.storage.session.set({ fa_session: saved }).catch(() => {});
}

// Record a value change (capturing the prior value) before applying it.
function recordChange(key: string, value: string): void {
  const old = chat?.facts.find((f) => f.key === key)?.value ?? null;
  if (old === value) return;
  recentChanges.push({ key, from: old, to: value });
  if (recentChanges.length > 8) recentChanges = recentChanges.slice(-8);
}

function upsertFact(key: string, value: string): void {
  if (!chat) return;
  const existing = chat.facts.find((f) => f.key === key);
  if (existing) existing.value = value;
  else chat.facts.push({ key, value, sensitivity: PUBLIC_KEYS.has(key) ? "public" : "personal" });
  saveSession();
}

// One inbound WS envelope handler, shared by first-connect and reconnect.
function makeOnMessage(runId: string): (event: MessageEvent) => Promise<void> {
  let inboundSeq = 0;
  return async (event: MessageEvent) => {
    const env = JSON.parse(event.data as string);
    if (env.type === "fill_result" || env.type === "fill_error") {
      if (chat) chat.busy = false;
      chrome.runtime.sendMessage({ type: "FA_FILL_DONE", result: env });
      return;
    }
    if (env.type === "chat_result") {
      if (chat) {
        chat.busy = false;
        for (const u of (env.applied as { key: string; value: string }[]) ?? []) {
          recordChange(u.key, u.value); // capture prior value before applying
          upsertFact(u.key, u.value);
        }
      }
      if (env.reply) agentMsg(String(env.reply));
      chrome.runtime.sendMessage({ type: "FA_FILL_DONE", result: env });
      return;
    }
    if (env.type === "chat_error") {
      if (chat) chat.busy = false;
      chrome.runtime.sendMessage({
        type: "FA_FILL_DONE",
        result: { type: "fill_error", error: String(env.error) },
      });
      return;
    }
    if (env.type === "document_facts") {
      // The backend returns the MERGED profile (existing + new, deduped) plus
      // any conflicts. Adopt the merged fields as our fact set and let the panel
      // render the update + conflicts.
      const fields = (env.fields as { key: string; value: string }[]) ?? [];
      if (chat) {
        chat.facts = fields.map((f) => ({
          key: f.key,
          value: f.value,
          sensitivity: PUBLIC_KEYS.has(f.key) ? "public" : "personal",
        }));
        saveSession();
      }
      chrome.runtime.sendMessage({
        type: "FA_DOC_FACTS",
        filename: env.filename,
        fields,
        conflicts: env.conflicts ?? [],
        added: env.added ?? [],
      });
      return;
    }
    if (env.type === "document_error") {
      chrome.runtime.sendMessage({ type: "FA_DOC_ERROR", error: String(env.error) });
      return;
    }
    const payload = env.payload ?? {};
    const reply: Record<string, unknown> = {
      protocol_version: env.protocol_version,
      message_type: "action_result",
      run_id: runId,
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
      chrome.runtime.sendMessage({
        type: "FA_FILL_PROGRESS",
        field: outcome.result.action_id,
        status: outcome.result.status,
        verification: outcome.verification?.status ?? null,
      });
    }
    chat?.ws.send(JSON.stringify(reply));
  };
}

// Open (or reopen) the backend WebSocket for a run; resolves the live session
// once open, or null on failure. Also stores it as the module `chat`.
function connectWs(
  backendUrl: string,
  runId: string,
  token: string,
  facts: ChatFact[],
): Promise<ChatSession | null> {
  return new Promise((resolve) => {
    let settled = false;
    const done = (s: ChatSession | null) => {
      if (!settled) {
        settled = true;
        resolve(s);
      }
    };
    const wsUrl = `${backendUrl.replace(/^http/, "ws")}/ws/${runId}?token=${token}`;
    const ws = new WebSocket(wsUrl);
    const session: ChatSession = { ws, runId, token, backendUrl, facts, busy: false };
    chat = session;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", origin: state.origin, tab_id: state.tabId }));
      done(session);
    };
    ws.onmessage = makeOnMessage(runId);
    ws.onclose = () => {
      if (chat?.ws === ws) chat = null;
      done(null);
    };
    ws.onerror = () => done(null);
  });
}

// Ensure a live backend connection, transparently reconnecting from persisted
// state if the service worker was recycled since the last turn.
async function ensureConnection(): Promise<boolean> {
  if (chat && chat.ws.readyState === WebSocket.OPEN) return true;
  let saved: SavedSession | undefined;
  try {
    saved = (await chrome.storage.session.get("fa_session")).fa_session as SavedSession | undefined;
  } catch {
    saved = undefined;
  }
  if (!saved || saved.tabId === null) return false;
  // Restore the guard session and re-inject the content script (it may have
  // survived, but re-injection is safe), then reconnect to the same run.
  state.attached = true;
  state.runId = saved.runId;
  state.tabId = saved.tabId;
  state.origin = saved.origin;
  state.error = null;
  recentChanges = saved.history ?? [];
  try {
    await chrome.scripting.executeScript({ target: { tabId: saved.tabId }, files: ["content.js"] });
  } catch {
    /* content script already present */
  }
  return (await connectWs(saved.backendUrl, saved.runId, saved.token, saved.facts ?? [])) !== null;
}

async function refill(): Promise<void> {
  if (!(await ensureConnection()) || !chat) {
    agentMsg("No active session — click “Fill this form” to start.");
    return;
  }
  if (chat.busy) {
    agentMsg("Still working on the last request — one moment.");
    return;
  }
  chat.busy = true;
  // Each backend fill/edit starts its own action sequence at 1; reset the
  // guard's counter so those actions aren't rejected as stale (the session is
  // reused across turns without re-attaching).
  lastActionSeq = 0;
  chat.ws.send(JSON.stringify({ type: "start_fill", facts: chat.facts }));
  chrome.runtime.sendMessage({ type: "FA_FILL_STARTED" });
}

// Attach the active tab, create a backend run, and open the WebSocket — the
// shared setup for both a fill and a document-first parse. Returns the live
// session or a human-readable error.
async function startSession(
  backendUrl: string,
  facts: ChatFact[],
): Promise<{ session: ChatSession } | { error: string }> {
  // A fresh session supersedes any prior conversation.
  if (chat) {
    try {
      chat.ws.close();
    } catch {
      /* ignore */
    }
    chat = null;
  }
  recentChanges = [];
  try {
    await chrome.storage.session.remove("fa_session");
  } catch {
    /* ignore */
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) return { error: "No active tab. Open the form in a tab first." };
  if (!/^https?:/.test(tab.url)) {
    return { error: `Cannot attach to ${tab.url} — open a normal web page with a form.` };
  }
  try {
    await attachToTab(tab.id, tab.url);
  } catch (e) {
    return { error: `Could not attach to this tab: ${e instanceof Error ? e.message : String(e)}` };
  }

  let run_id: string;
  let session_token: string;
  try {
    const runResp = await fetch(`${backendUrl}/runs`, { method: "POST" });
    if (!runResp.ok) return { error: `Backend returned ${runResp.status}. Is it running at ${backendUrl}?` };
    ({ run_id, session_token } = await runResp.json());
  } catch {
    return {
      error: `Cannot reach the backend at ${backendUrl}. Start it: uv run python -m agent_backend.api.server`,
    };
  }
  state.runId = run_id;

  const session = await connectWs(backendUrl, run_id, session_token, facts);
  if (!session) return { error: "backend connection failed (is it running?)" };
  saveSession();
  return { session };
}

// Ensure there is a live session for a document parse (which can happen before
// any fill): reuse an open one, reconnect a recycled one, or start a new one.
async function sessionForDocument(backendUrl: string): Promise<ChatSession | null> {
  if (chat && chat.ws.readyState === WebSocket.OPEN) return chat;
  if (await ensureConnection()) return chat;
  const result = await startSession(backendUrl, []);
  if ("error" in result) {
    agentMsg(result.error);
    return null;
  }
  return result.session;
}

// Product flow: drive a fill in the active tab via the backend over a
// WebSocket. The socket is kept open (and reconnectable) for follow-up chat.
async function fillViaBackend(facts: ChatFact[], backendUrl: string): Promise<void> {
  const result = await startSession(backendUrl, facts);
  if ("error" in result) {
    chrome.runtime.sendMessage({ type: "FA_FILL_DONE", result: { type: "fill_error", error: result.error } });
    return;
  }
  const session = result.session;
  session.busy = true;
  lastActionSeq = 0;
  session.ws.send(JSON.stringify({ type: "start_fill", facts }));
  chrome.runtime.sendMessage({ type: "FA_FILL_STARTED" });
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
    if (message?.type === "FA_ANSWER") {
      // A direct answer to a question: record the fact and re-fill (reconnecting
      // first if the worker was recycled).
      void (async () => {
        if (!(await ensureConnection()) || !chat) {
          agentMsg("No active session — click “Fill this form” to start.");
          return;
        }
        recordChange(message.key, message.value);
        upsertFact(message.key, message.value);
        agentMsg(`Got it — ${message.key} = ${message.value}. Re-filling…`);
        await refill();
      })();
      sendResponse(state);
      return false;
    }
    if (message?.type === "FA_PARSE_DOC") {
      void (async () => {
        // Works even before a fill: build the profile from the document first.
        const session = await sessionForDocument("http://127.0.0.1:8000");
        if (!session) return;
        agentMsg(`Reading ${message.filename}…`);
        session.ws.send(
          JSON.stringify({
            type: "parse_document",
            filename: message.filename,
            mime_type: message.mimeType,
            content_base64: message.contentBase64,
            facts: message.facts ?? [],
          }),
        );
      })();
      sendResponse(state);
      return false;
    }
    if (message?.type === "FA_CHAT") {
      // Reasoning happens on the backend (it has the model + the live form
      // state). Reconnect if needed, then forward with the current facts.
      void (async () => {
        if (!(await ensureConnection()) || !chat) {
          agentMsg("No active session — click “Fill this form” to start.");
          return;
        }
        if (chat.busy) {
          agentMsg("Still working on the last request — one moment.");
          return;
        }
        chat.busy = true;
        lastActionSeq = 0; // fresh action sequence for this edit (see refill)
        chat.ws.send(
          JSON.stringify({
            type: "chat",
            text: message.text,
            facts: chat.facts,
            history: recentChanges,
          }),
        );
        chrome.runtime.sendMessage({ type: "FA_FILL_STARTED" });
      })();
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
