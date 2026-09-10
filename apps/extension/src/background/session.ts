// The conversational backend session: a WebSocket to the local backend over
// which the backend drives observe/execute against the tab (via ./tab) and the
// user's fill/chat/document requests are sent.
//
// The socket lives in this MV3 service worker, which Chrome terminates when
// idle — so the essentials are mirrored to chrome.storage.session and the
// socket is transparently RE-ESTABLISHED on the next command. The fact list
// held here is the source of truth sent to the backend on each turn.
import type { ChatFact } from "../shared/messages";
import { sensitivityFor, upsertFact as upsertInto } from "../shared/facts";
import {
  attachToTab,
  execute,
  observe,
  resetActionSequence,
  restoreSession,
  state,
} from "./tab";

export const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

type ChatSession = {
  ws: WebSocket;
  runId: string;
  token: string;
  backendUrl: string;
  facts: ChatFact[];
  busy: boolean;
};
let chat: ChatSession | null = null;

// A short trail of value changes so the agent can honor "change it back to the
// previous value". Sent to the interpreter with each chat turn.
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

const NO_SESSION_MSG = "No active session — click “Fill this form” to start.";
const BUSY_MSG = "Still working on the last request — one moment.";

// -- panel messaging --------------------------------------------------------

function toPanel(message: Record<string, unknown>): void {
  chrome.runtime.sendMessage(message);
}

function agentMsg(text: string): void {
  toPanel({ type: "FA_AGENT_MSG", text });
}

// -- persistence -----------------------------------------------------------

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

async function loadSavedSession(): Promise<SavedSession | undefined> {
  try {
    return (await chrome.storage.session.get("fa_session")).fa_session as SavedSession | undefined;
  } catch {
    return undefined;
  }
}

async function clearSavedSession(): Promise<void> {
  try {
    await chrome.storage.session.remove("fa_session");
  } catch {
    /* ignore */
  }
}

// -- facts -----------------------------------------------------------------

/** Record a value change (capturing the prior value) before applying it. */
function recordChange(key: string, value: string): void {
  const old = chat?.facts.find((f) => f.key === key)?.value ?? null;
  if (old === value) return;
  recentChanges.push({ key, from: old, to: value });
  if (recentChanges.length > 8) recentChanges = recentChanges.slice(-8);
}

function setFact(key: string, value: string): void {
  if (!chat) return;
  upsertInto(chat.facts, key, value);
  saveSession();
}

// -- inbound envelopes -----------------------------------------------------

/** Handle one envelope from the backend: a result for the panel, or a command
 * (observe/execute) to carry out against the tab and reply to. */
function makeOnMessage(runId: string): (event: MessageEvent) => Promise<void> {
  let inboundSeq = 0;
  return async (event: MessageEvent) => {
    const env = JSON.parse(event.data as string);
    switch (env.type) {
      case "fill_result":
      case "fill_error":
        if (chat) chat.busy = false;
        toPanel({ type: "FA_FILL_DONE", result: env });
        return;
      case "chat_result":
        if (chat) {
          chat.busy = false;
          for (const u of (env.applied as { key: string; value: string }[]) ?? []) {
            recordChange(u.key, u.value);
            setFact(u.key, u.value);
          }
        }
        if (env.reply) agentMsg(String(env.reply));
        toPanel({ type: "FA_FILL_DONE", result: env });
        return;
      case "chat_error":
        if (chat) chat.busy = false;
        toPanel({ type: "FA_FILL_DONE", result: { type: "fill_error", error: String(env.error) } });
        return;
      case "document_facts": {
        // The backend returns the MERGED profile plus any conflicts; adopt the
        // merged fields as our fact set and let the panel render them.
        const fields = (env.fields as { key: string; value: string }[]) ?? [];
        if (chat) {
          chat.facts = fields.map((f) => ({ ...f, sensitivity: sensitivityFor(f.key) }));
          saveSession();
        }
        toPanel({
          type: "FA_DOC_FACTS",
          filename: env.filename,
          fields,
          conflicts: env.conflicts ?? [],
          added: env.added ?? [],
        });
        return;
      }
      case "document_error":
        toPanel({ type: "FA_DOC_ERROR", error: String(env.error) });
        return;
      default:
        break; // a command from the backend driver, handled below
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
      toPanel({
        type: "FA_FILL_PROGRESS",
        field: outcome.result.action_id,
        status: outcome.result.status,
        verification: outcome.verification?.status ?? null,
      });
    }
    chat?.ws.send(JSON.stringify(reply));
  };
}

// -- connection lifecycle --------------------------------------------------

/** Open (or reopen) the backend WebSocket for a run; resolves the live session
 * once open, or null on failure. Also stores it as the module `chat`. */
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
    const ws = new WebSocket(`${backendUrl.replace(/^http/, "ws")}/ws/${runId}?token=${token}`);
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

/** Ensure a live backend connection, transparently reconnecting from persisted
 * state if the service worker was recycled since the last turn. */
async function ensureConnection(): Promise<boolean> {
  if (chat && chat.ws.readyState === WebSocket.OPEN) return true;
  const saved = await loadSavedSession();
  if (!saved || saved.tabId === null) return false;
  // Restore the guard session and re-inject the content script (re-injection
  // is safe if it survived), then reconnect to the same run.
  restoreSession(saved.runId, saved.tabId, saved.origin);
  recentChanges = saved.history ?? [];
  try {
    await chrome.scripting.executeScript({ target: { tabId: saved.tabId }, files: ["content.js"] });
  } catch {
    /* content script already present */
  }
  return (await connectWs(saved.backendUrl, saved.runId, saved.token, saved.facts ?? [])) !== null;
}

/** Attach the active tab, create a backend run, and open the WebSocket — the
 * shared setup for both a fill and a document-first parse. */
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
  await clearSavedSession();

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

/** A live session for a document parse (which can happen before any fill):
 * reuse an open one, reconnect a recycled one, or start a new one. */
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

/** Send a backend operation on the live session (a fill or a chat turn). */
function sendOperation(session: ChatSession, envelope: Record<string, unknown>): void {
  session.busy = true;
  resetActionSequence(); // each backend fill/edit numbers its actions from 1
  session.ws.send(JSON.stringify(envelope));
  toPanel({ type: "FA_FILL_STARTED" });
}

async function liveSession(): Promise<ChatSession | null> {
  if (!(await ensureConnection()) || !chat) {
    agentMsg(NO_SESSION_MSG);
    return null;
  }
  if (chat.busy) {
    agentMsg(BUSY_MSG);
    return null;
  }
  return chat;
}

// -- panel commands --------------------------------------------------------

/** "Fill this form": start a fresh session and drive a full fill. */
export async function fill(facts: ChatFact[], backendUrl: string): Promise<void> {
  const result = await startSession(backendUrl, facts);
  if ("error" in result) {
    toPanel({ type: "FA_FILL_DONE", result: { type: "fill_error", error: result.error } });
    return;
  }
  sendOperation(result.session, { type: "start_fill", facts });
}

/** A direct answer to a question: record the fact and re-fill. */
export async function answer(key: string, value: string): Promise<void> {
  const session = await liveSession();
  if (!session) return;
  recordChange(key, value);
  setFact(key, value);
  agentMsg(`Got it — ${key} = ${value}. Re-filling…`);
  sendOperation(session, { type: "start_fill", facts: session.facts });
}

/** A free-text message: the backend reasons over the live form and acts. */
export async function chatTurn(text: string): Promise<void> {
  const session = await liveSession();
  if (!session) return;
  sendOperation(session, { type: "chat", text, facts: session.facts, history: recentChanges });
}

/** Parse an uploaded document into the profile (works before any fill). */
export async function parseDocument(doc: {
  filename: string;
  mimeType: string;
  contentBase64: string;
  facts: ChatFact[];
}): Promise<void> {
  const session = await sessionForDocument(DEFAULT_BACKEND_URL);
  if (!session) return;
  agentMsg(`Reading ${doc.filename}…`);
  session.ws.send(
    JSON.stringify({
      type: "parse_document",
      filename: doc.filename,
      mime_type: doc.mimeType,
      content_base64: doc.contentBase64,
      facts: doc.facts ?? [],
    }),
  );
}
