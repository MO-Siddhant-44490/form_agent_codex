// Background service worker entry: routes side-panel commands to the tab
// session (./tab — attach/observe/execute behind the guards) and the backend
// chat session (./session — fill, chat, answer, document parse).
import type { ApprovalToken } from "@form-agent/contracts";
import type { ExecuteOutcome, PanelCommand, SessionState } from "../shared/messages";
import {
  DEFAULT_BACKEND_URL,
  answer,
  bindFact,
  chatTurn,
  fill,
  parseDocument,
  setField,
} from "./session";
import {
  attachActiveTab,
  attachToTab,
  execute,
  grantApproval,
  observe,
  setFixtureMode,
  state,
} from "./tab";

chrome.runtime.onMessage.addListener(
  (message: PanelCommand, _sender, sendResponse: (s: SessionState) => void) => {
    switch (message?.type) {
      case "FA_ATTACH_ACTIVE_TAB":
        attachActiveTab().then(sendResponse);
        return true; // async response
      case "FA_OBSERVE_NOW":
        observe().then(sendResponse);
        return true;
      case "FA_GET_STATE":
        sendResponse(state);
        return false;
      case "FA_FILL":
        void fill(message.facts ?? [], message.backendUrl ?? DEFAULT_BACKEND_URL);
        break;
      case "FA_ANSWER":
        void answer(message.key, message.value);
        break;
      case "FA_CHAT":
        void chatTurn(message.text);
        break;
      case "FA_BIND":
        void bindFact(message.fieldId, message.key);
        break;
      case "FA_SET_FIELD":
        void setField(message.fieldId, message.value);
        break;
      case "FA_PARSE_DOC":
        void parseDocument(message);
        break;
      default:
        return false;
    }
    // Fire-and-forget commands report progress/results via runtime messages.
    sendResponse(state);
    return false;
  },
);

chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => {});

// Test hooks: lets the Playwright harness drive the loop from the
// service-worker context, standing in for the side panel's user gesture and
// the backend. Excluded from any store build.
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
  setFixtureMode,
  grantApproval,
};
