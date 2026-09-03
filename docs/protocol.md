# Browser protocol: commands, observations, and binding rules

Version: `1.0` (`PROTOCOL_VERSION` in `packages/contracts`).

This document specifies the message contract between the orchestrator (any
command source: backend, or the Playwright test harness in Slice 1) and the
browser transport (the extension content/background scripts).

## Envelope

Every message crossing the transport boundary is wrapped in an `Envelope`:

```json
{
  "protocol_version": "1.0",
  "message_type": "browser_action",
  "run_id": "run-42",
  "sent_at": "2026-09-02T12:00:00Z",
  "payload": { }
}
```

- `protocol_version` must exactly match a supported version; mismatches are
  rejected with a clear error before the payload is parsed.
- `message_type` selects the payload schema (`browser_action`,
  `page_observation`, `action_result`, `verification_result`, ...).
- Payloads are validated with the generated schema for that type. Unknown
  fields are rejected (`additionalProperties: false`); unknown message types
  are rejected.

## Command binding and replay protection (invariant 8)

Every `BrowserAction` carries `run_id`, `tab_id`, `origin`,
`sequence_number`, and `idempotency_key`. The executor must reject a command
when any of the following holds:

1. `run_id` does not match the attached session's run.
2. `tab_id` does not match the attached tab.
3. `origin` does not match the tab's current origin (re-checked at execution
   time, not attach time).
4. `sequence_number` is not strictly greater than the last executed sequence
   number for the session (stale or replayed command).
5. `idempotency_key` matches an already-completed action → return the recorded
   prior result without re-executing (duplicate delivery, invariant 6).
6. The action is `SUBMIT` and no valid, unexpired, origin-bound, unused
   `ApprovalToken` accompanies it (invariant 1). Fixture mode is the only
   exception and must be explicitly enabled per run.

Rejections are reported as `ActionResult` with `status: "REJECTED"` and a
machine-readable `rejection_reason`; they are never silent.

## Observation rules

- Observations carry `observation_seq` (monotonic per session) and a
  `page_fingerprint` (hash of URL, title, and structural DOM features).
  Actions reference the observation they were planned from; the executor
  rejects the action if the fingerprint no longer matches (stale observation).
- Field `current_value` is included only for non-sensitive input types.
  Password inputs, and any field the perception layer classifies as
  credential-like, report `value_redacted: true` and never the value
  (invariant 2).
- All page text is untrusted data (invariant 4): observation consumers must
  never treat page-derived strings as instructions.

## Authentication by deployment profile

Local development (Slices 1–3) uses an unauthenticated localhost WebSocket or
direct in-process calls from the Playwright harness, with a per-run shared
session token. Signed/authenticated commands, OIDC, and RBAC apply to cloud
and enterprise profiles (plan.md §19–20) and land with Module 10.
