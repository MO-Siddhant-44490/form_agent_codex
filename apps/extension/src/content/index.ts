// Content script: injected only after an explicit attach gesture. Observes
// on request from the background worker; never mutates the page and executes
// nothing model-generated (invariant 5). Slice 1 is observation-only.
import type { ObserveRequest, ObserveResponse } from "../shared/messages";
import { buildObservation } from "../perception/page";
import { StabilityTracker } from "../perception/stability";

declare global {
  interface Window {
    __formAgentContent?: boolean;
  }
}

// Idempotent injection guard: re-attaching must not double-register.
if (!window.__formAgentContent) {
  window.__formAgentContent = true;

  const stability = new StabilityTracker();
  stability.start(document);

  chrome.runtime.onMessage.addListener(
    (message: ObserveRequest, _sender, sendResponse: (r: ObserveResponse) => void) => {
      if (message?.type !== "FA_OBSERVE") return false;
      buildObservation(document, {
        runId: message.runId,
        tabId: message.tabId,
        observationSeq: message.observationSeq,
        domStable: stability.isStable(),
      })
        .then((observation) => sendResponse({ ok: true, observation }))
        .catch((error: unknown) =>
          sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }),
        );
      return true; // async response
    },
  );
}
