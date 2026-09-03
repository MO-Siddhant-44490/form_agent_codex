// Background service worker: owns the tab session, routes observe requests,
// and validates every observation against the generated contract schema
// before trusting it. Slice 1: observation only — there is no action path.
import { PageObservationSchema, type PageObservation } from "@form-agent/contracts";
import type { ObserveRequest, ObserveResponse, PanelCommand, SessionState } from "../shared/messages";

const state: SessionState = {
  attached: false,
  runId: null,
  tabId: null,
  origin: null,
  lastObservation: null,
  error: null,
};
let observationSeq = 0;

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
  return state;
}

async function observe(): Promise<SessionState> {
  if (!state.attached || state.tabId === null || state.runId === null) {
    state.error = "not attached";
    return state;
  }
  // Origin re-validation on every observation (invariant 8 / plan.md §5.1):
  // if the tab navigated cross-origin, drop the session instead of observing.
  const tab = await chrome.tabs.get(state.tabId);
  const currentOrigin = tab.url ? new URL(tab.url).origin : null;
  if (currentOrigin !== state.origin) {
    state.attached = false;
    state.error = `origin changed (${state.origin} -> ${currentOrigin}); re-attach required`;
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
    return false;
  },
);

chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => {});

// Test hooks: lets the Playwright harness drive attach/observe from the
// service-worker context, standing in for the user gesture that dev/test
// host_permissions make unnecessary. Excluded from any store build.
declare global {
  // eslint-disable-next-line no-var
  var __formAgentTest: {
    attachToTab(tabId: number, url: string): Promise<SessionState>;
    observe(): Promise<SessionState>;
    getState(): SessionState;
  };
}
globalThis.__formAgentTest = {
  attachToTab,
  observe,
  getState: () => state,
};
