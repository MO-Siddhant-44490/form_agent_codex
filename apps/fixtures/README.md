# Controlled form fixtures

Reproducible benchmark forms for CI, security tests, and the Slice 1 loop
(plan.md Module 1, §16 Level 2). Rules:

- Every fixture ships `ground-truth.json`: the discoverable fields, expected
  values for the canonical fact set, and honeypots that must never be filled.
- Fixtures never perform an external side effect. Submission is recorded
  locally in `window.__fixture.submissions` and rendered into the page.
- Reset is deterministic: reloading the page restores the initial state;
  `window.__fixture.reset()` does the same without a reload.
- Serve with `pnpm --filter @form-agent/fixtures serve`
  (http://127.0.0.1:4173/basic-form/).

Fixture-mode submission is the only permitted automated submission
(AGENTS.md invariant 1).
