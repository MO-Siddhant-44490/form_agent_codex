// Content script: injected only after an explicit attach gesture. Observes
// and executes validated typed actions on request from the background worker;
// executes nothing model-generated directly (invariant 5).
import type {
  ExecuteRequest,
  ExecuteResponse,
  ObserveRequest,
  ObserveResponse,
} from "../shared/messages";
import { executeAction } from "../actions/execute";
import { buildObservation } from "../perception/page";
import { StabilityTracker } from "../perception/stability";

declare global {
  interface Window {
    __formAgentContent?: boolean;
  }
}

type AnyRequest = ObserveRequest | ExecuteRequest;

// Idempotent injection guard: re-attaching must not double-register.
if (!window.__formAgentContent) {
  window.__formAgentContent = true;

  const stability = new StabilityTracker();
  stability.start(document);

  let lastFingerprint: string | null = null;

  async function handleObserve(message: ObserveRequest): Promise<ObserveResponse> {
    const observation = await buildObservation(document, {
      runId: message.runId,
      tabId: message.tabId,
      observationSeq: message.observationSeq,
      domStable: stability.isStable(),
    });
    lastFingerprint = observation.page_fingerprint;
    return { ok: true, observation };
  }

  async function waitForStablePage(timeoutMs = 8000): Promise<void> {
    const start = Date.now();
    while (!stability.isStable() && Date.now() - start < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  async function handleExecute(message: ExecuteRequest): Promise<ExecuteResponse> {
    const action = message.action;
    // Live origin check at execution time, not attach time (invariant 8).
    if (action.origin !== window.location.origin) {
      return {
        ok: true,
        result: {
          action_id: action.action_id,
          status: "REJECTED",
          rejection_reason: "origin_mismatch",
          error: `page origin is ${window.location.origin}`,
          executed_at: new Date().toISOString(),
        },
      };
    }
    // Stale-page check: the page must still match the observation the action
    // was planned from.
    if (message.expectedFingerprint !== null && lastFingerprint !== message.expectedFingerprint) {
      return {
        ok: true,
        result: {
          action_id: action.action_id,
          status: "REJECTED",
          rejection_reason: "stale_observation",
          error: "page fingerprint changed since planning observation",
          executed_at: new Date().toISOString(),
        },
      };
    }
    if (action.kind === "WAIT_FOR_STABLE_PAGE") await waitForStablePage();
    return { ok: true, result: executeAction(document, action) };
  }

  chrome.runtime.onMessage.addListener(
    (message: AnyRequest, _sender, sendResponse: (r: ObserveResponse | ExecuteResponse) => void) => {
      const handler =
        message?.type === "FA_OBSERVE"
          ? handleObserve(message)
          : message?.type === "FA_EXECUTE"
            ? handleExecute(message)
            : null;
      if (!handler) return false;
      handler
        .then(sendResponse)
        .catch((error: unknown) =>
          sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }),
        );
      return true; // async response
    },
  );
}
