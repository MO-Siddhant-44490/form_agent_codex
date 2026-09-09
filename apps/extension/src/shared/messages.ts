// Internal extension message contracts (background <-> content <-> sidepanel).
// Cross-boundary payloads reuse the generated protocol types.
import type { ActionResult, BrowserAction, PageObservation, VerificationResult } from "@form-agent/contracts";

export type ObserveRequest = {
  type: "FA_OBSERVE";
  runId: string;
  tabId: number;
  observationSeq: number;
};

export type ObserveResponse =
  | { ok: true; observation: PageObservation }
  | { ok: false; error: string };

export type ExecuteRequest = {
  type: "FA_EXECUTE";
  action: BrowserAction;
  // Fingerprint of the observation the action was planned from; the content
  // script refuses to act on a page that no longer matches (invariant 8).
  expectedFingerprint: string | null;
};

export type ExecuteResponse =
  | { ok: true; result: ActionResult }
  | { ok: false; error: string };

export type ChatFact = { key: string; value: string; sensitivity: string };

export type PanelCommand =
  | { type: "FA_ATTACH_ACTIVE_TAB" }
  | { type: "FA_OBSERVE_NOW" }
  | { type: "FA_GET_STATE" }
  | { type: "FA_FILL"; facts: ChatFact[]; backendUrl?: string }
  // Free-text chat: a correction/command the agent parses ("set state to X",
  // "refill"). Handled in the background against the open session.
  | { type: "FA_CHAT"; text: string }
  // A direct answer to a question: upsert this fact and re-fill.
  | { type: "FA_ANSWER"; key: string; value: string };

export type SessionState = {
  attached: boolean;
  runId: string | null;
  tabId: number | null;
  origin: string | null;
  lastObservation: PageObservation | null;
  error: string | null;
};

export type ExecuteOutcome = {
  result: ActionResult;
  verification: VerificationResult | null;
  state: SessionState;
};
