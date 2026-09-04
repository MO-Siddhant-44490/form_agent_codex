# Evaluations

Provider-neutral, model-only cases (plan.md §18). Run one adapter over the
dataset and get accuracy/safety/clarification metrics.

```bash
# offline, deterministic
uv run python evals/runners/run_mapping_eval.py --adapter fake

# real Bedrock (needs: aws sso login --profile dev)
AWS_PROFILE=dev uv run python evals/runners/run_mapping_eval.py \
  --adapter bedrock --model-id apac.anthropic.claude-sonnet-4-20250514-v1:0
```

Reports land in `evals/reports/` (gitignored).

## Mapping baseline

`datasets/mapping_cases.json` — 8 cases: exact/synonym labels, enum option
selection, missing-fact-must-ask, two-address ambiguity, prompt-injection
label, credential-field-forbidden.

| Adapter | Model | Mapping acc | Option acc | Unsafe | Schema fail | Missed clarif |
| --- | --- | --- | --- | --- | --- | --- |
| fake | fake-mapper-v1 | 1.00 | 1.00 | 0 | 0 | 0 |
| bedrock | apac Claude Sonnet 4 | 0.90 | 1.00 | 0 | 0 | 1 |

Recorded 2026-09-03, ap-south-1. The one missed clarification is the
two-address case: the model maps the single `address` fact to "current
address" and asks about "permanent" — defensible; the eval label is
conservative (expects both to ask). Zero unsafe mappings: the credential
field is never mapped, and the deterministic policy gate blocks it regardless.


## Shadow mode, ablations, and benchmark (offline, no creds)

```bash
uv run python evals/runners/run_shadow_eval.py     # propose-only metrics + safety gate
uv run python evals/runners/run_ablation.py        # ablation grid + false-success rates
uv run python evals/runners/run_benchmark.py       # consolidated JSON + Markdown report
```

Shadow mode observes a page and proposes a full typed plan WITHOUT executing —
safe on held-out live sites (point it at the extension transport). The ablation
study shows the open-loop config's non-zero false-success rate vs the closed
loop's zero, and recovery completing flaky fields that no-recovery abandons.

## Model comparison (live Bedrock)

```bash
aws sso login --profile dev
AWS_PROFILE=dev uv run python evals/runners/run_model_comparison.py \
    --models apac.anthropic.claude-sonnet-4-20250514-v1:0 \
             apac.anthropic.claude-3-7-sonnet-20250219-v1:0 \
             apac.anthropic.claude-3-haiku-20240307-v1:0
```

Runs the mapping cases across each model and reports accuracy, option accuracy,
unsafe mappings, schema failures, latency, and token usage side by side.

## Live-site shadow mode (authorized sites only)

Shadow mode is side-effect-free, so it is safe on any page you are authorized
to test (public automation-practice sites, owned/sandbox sites, or forms with
explicit permission). To run against an external site:

1. Add the site's host to a DEV extension build's `host_permissions` (not
   `<all_urls>` — just that domain), then `pnpm --filter @form-agent/extension build`.
2. Use `ExtensionPlaywrightTransport(..., extra_args=["--ignore-certificate-errors"])`
   if behind a TLS-inspecting corporate proxy.
3. `propose_plan(transport, facts, mapper=ModelAssistedMapper(bedrock))` — it
   observes and proposes only; it never executes or navigates.

Verified live on https://httpbin.org/forms/post: perception found all 10
fields; Bedrock mapped custname/custtel/custemail to full_name/phone/email;
zero unsafe proposals; the page was not modified.


## Model comparison baseline (live Bedrock, ap-south-1)

8 provider-neutral mapping cases, recorded 2026-09-04:

| Model | Mapping acc | Option acc | Unsafe | Schema fail | Median latency | Output tokens |
| --- | --- | --- | --- | --- | --- | --- |
| APAC Claude Sonnet 4 | 0.90 | 1.00 | 0 | 0 | 3236 ms | 1038 |
| APAC Claude 3.7 Sonnet | 0.90 | 1.00 | 0 | 0 | 2102 ms | 933 |
| APAC Claude 3.5 Sonnet v2 | 0.90 | 1.00 | 0 | 0 | 2534 ms | 1013 |
| APAC Claude 3 Haiku | 0.70 | 1.00 | 0 | 0 | 1259 ms | 843 |

The Sonnet-class models tie at 0.90 mapping accuracy (3.7 Sonnet is the fastest
of them); Haiku is fastest but drops to 0.70. Every model produced zero unsafe
mappings — the deterministic policy gate holds regardless of model choice.

## Live-site validation achieved (httpbin.org/forms/post)

- Level 3 (shadow / propose-only): perception found all 10 fields; Bedrock
  mapped custname/custtel/custemail to full_name/phone/email; 0 unsafe
  proposals; page unmodified.
- Level 4 (supervised fill): the closed loop filled and verified the 3 mapped
  fields on the live page (all verifications SUCCESS) and STOPPED before
  submission — no SUBMIT issued, nothing sent.
