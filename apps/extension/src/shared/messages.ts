// Internal extension message contracts (background <-> content <-> sidepanel).
// Cross-boundary payloads reuse the generated protocol types.
import type { PageObservation } from "@form-agent/contracts";

export type ObserveRequest = {
  type: "FA_OBSERVE";
  runId: string;
  tabId: number;
  observationSeq: number;
};

export type ObserveResponse =
  | { ok: true; observation: PageObservation }
  | { ok: false; error: string };

export type PanelCommand =
  | { type: "FA_ATTACH_ACTIVE_TAB" }
  | { type: "FA_OBSERVE_NOW" }
  | { type: "FA_GET_STATE" };

export type SessionState = {
  attached: boolean;
  runId: string | null;
  tabId: number | null;
  origin: string | null;
  lastObservation: PageObservation | null;
  error: string | null;
};
