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
