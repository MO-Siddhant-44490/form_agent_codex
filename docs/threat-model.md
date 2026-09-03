# Threat model (initial)

Scope: Slice 1 surface — extension perception/action/verification on
controlled fixtures, driven by a local harness. Expanded per slice.

## Assets

- User document facts (PII) and their provenance.
- The user's authenticated browser session and cookies.
- The integrity of the target form (what actually gets typed/submitted).
- Credentials/OTP/CAPTCHA interactions (explicitly out of bounds — never
  collected, invariant 2).

## Adversaries and threats

| # | Threat | Vector | Mitigation |
|---|--------|--------|------------|
| T1 | Malicious page steers the agent (prompt injection) | Page text, labels, hidden elements instructing the model | All page content typed as untrusted data; models emit only typed domain objects validated locally; deterministic policy gate before every action (invariants 4, 5) |
| T2 | Hidden honeypot fields harvest data or flag bots | Visually hidden inputs | Perception filters non-visible elements; policy blocks writes to hidden fields |
| T3 | Command replay / cross-tab injection | Duplicate, stale, or cross-run commands reaching the executor | run/tab/origin/sequence binding + idempotency records (invariants 6, 8); rejections are explicit |
| T4 | Unintended or automated submission | Bug, model error, or page trickery triggering submit | Deterministic submission lock; SUBMIT requires unexpired, origin-bound, single-use approval token; fixture-only exception (invariant 1) |
| T5 | PII exfiltration via logs/traces/model requests | Facts or page values in telemetry or prompts | Redaction utilities on all trace paths; data minimization to models; sensitivity labels on facts (invariants 10, 11) |
| T6 | Credential capture | Password/OTP/CAPTCHA fields observed or filled | Perception marks credential fields value-redacted; policy hard-blocks filling them; human takeover flow (invariants 2, 3) |
| T7 | Origin drift mid-run | Navigation/redirect to an unexpected origin | Origin re-checked per command; cross-origin requires new user gesture or pre-approved permission |
| T8 | Runaway loops burning cost or hammering a site | Retry loops on failing actions | Hard budgets on retries/steps/model calls/time; classified terminal outcomes (invariant 12) |
| T9 | Compromised or spoofed backend commands | Untrusted command source | Local dev: localhost-only + per-run token; cloud/enterprise: authenticated TLS WebSocket, signed commands (Module 10) |

## Out of scope (by design, see plan.md §3)

CAPTCHA solving, anti-bot evasion, credential automation, and autonomous
submission to arbitrary live sites are not threats to mitigate but behaviors
the system must refuse.
