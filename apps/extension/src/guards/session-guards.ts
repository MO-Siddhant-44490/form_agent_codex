// Deterministic command guards (invariants 1, 6, 8; docs/protocol.md).
// Pure functions: the background worker owns the mutable records.
import type { ActionResult, ApprovalToken, BrowserAction } from "@form-agent/contracts";

export type GuardSession = {
  runId: string;
  tabId: number;
  origin: string;
  lastActionSeq: number;
  lastObservationSeq: number;
  fixtureMode: boolean;
};

export type GuardRecords = {
  completed: Map<string, ActionResult>; // idempotency_key -> prior result
  approvals: Map<string, ApprovalToken>; // token_id -> token
};

export type GuardVerdict =
  | { allowed: true; duplicate?: ActionResult }
  | { allowed: false; reason: ActionResult["rejection_reason"] & string; detail: string };

export function checkAction(
  session: GuardSession,
  records: GuardRecords,
  action: BrowserAction,
  now: Date = new Date(),
): GuardVerdict {
  if (action.run_id !== session.runId) {
    return { allowed: false, reason: "run_mismatch", detail: `expected ${session.runId}` };
  }
  if (action.tab_id !== session.tabId) {
    return { allowed: false, reason: "tab_mismatch", detail: `expected tab ${session.tabId}` };
  }
  if (action.origin !== session.origin) {
    return { allowed: false, reason: "origin_mismatch", detail: `expected ${session.origin}` };
  }
  // Duplicate delivery of an already-completed command returns the recorded
  // result without repeating the side effect (invariant 6). Checked before
  // the sequence guard so a redelivery is a DUPLICATE, not a stale rejection.
  if (action.idempotency_key) {
    const prior = records.completed.get(action.idempotency_key);
    if (prior) return { allowed: true, duplicate: prior };
  }

  if (action.sequence_number <= session.lastActionSeq) {
    return {
      allowed: false,
      reason: "stale_sequence",
      detail: `sequence ${action.sequence_number} <= last executed ${session.lastActionSeq}`,
    };
  }
  if (
    action.source_observation_seq !== null &&
    action.source_observation_seq !== undefined &&
    action.source_observation_seq !== session.lastObservationSeq
  ) {
    return {
      allowed: false,
      reason: "stale_observation",
      detail: `planned from observation ${action.source_observation_seq}, current is ${session.lastObservationSeq}`,
    };
  }

  // Submission lock (invariant 1): deterministic code, not schema, is the
  // final enforcement point.
  if (action.kind === "SUBMIT" && !session.fixtureMode) {
    const verdict = checkApproval(session, records, action, now);
    if (verdict) return verdict;
  }
  return { allowed: true };
}

function checkApproval(
  session: GuardSession,
  records: GuardRecords,
  action: BrowserAction,
  now: Date,
): GuardVerdict | null {
  const tokenId = action.approval_token_id;
  if (!tokenId) {
    return { allowed: false, reason: "missing_approval", detail: "SUBMIT without approval token" };
  }
  const token = records.approvals.get(tokenId);
  if (!token) {
    return { allowed: false, reason: "missing_approval", detail: "unknown approval token" };
  }
  if (token.used) {
    return { allowed: false, reason: "missing_approval", detail: "approval token already used" };
  }
  if (new Date(token.expires_at) <= now) {
    return { allowed: false, reason: "missing_approval", detail: "approval token expired" };
  }
  if (token.origin !== session.origin || token.run_id !== session.runId) {
    return {
      allowed: false,
      reason: "missing_approval",
      detail: "approval token bound to a different origin or run",
    };
  }
  return null;
}
