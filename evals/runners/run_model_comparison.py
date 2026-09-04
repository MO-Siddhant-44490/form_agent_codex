"""Model comparison (plan.md §18): run the provider-neutral mapping cases across
several models and produce one comparison table — mapping accuracy, option
accuracy, unsafe mappings, schema failures, latency, and token usage per model.

    aws sso login --profile dev
    AWS_PROFILE=dev uv run python evals/runners/run_model_comparison.py \
        --models apac.anthropic.claude-sonnet-4-20250514-v1:0 \
                 apac.anthropic.claude-3-7-sonnet-20250219-v1:0 \
                 apac.anthropic.claude-3-haiku-20240307-v1:0

With no --models it compares only the offline fake adapter (mechanics check)."""

import argparse

# Reuse the scoring + case loading from the single-adapter runner.
import importlib.util
import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path

from agent_backend.model_gateway.base import ModelUnavailable

_spec = importlib.util.spec_from_file_location(
    "run_mapping_eval", Path(__file__).with_name("run_mapping_eval.py")
)
_mapping = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mapping)

EVALS_DIR = Path(__file__).resolve().parents[1]
REPORTS = EVALS_DIR / "reports"


def evaluate_model(adapter_name: str, model_id: str | None) -> dict:
    adapter = _mapping.build_adapter(adapter_name, model_id)
    dataset = json.loads((EVALS_DIR / "datasets" / "mapping_cases.json").read_text())
    scored, latencies, in_tok, out_tok, schema_failures = [], [], 0, 0, 0
    for case in dataset["cases"]:
        try:
            result = adapter.map_fields(_mapping.request_from_case(case))
        except ModelUnavailable:
            schema_failures += 1
            continue
        scored.append(_mapping.score_case(case, result.batch.mappings))
        latencies.append(result.metadata.latency_ms)
        in_tok += result.metadata.input_tokens or 0
        out_tok += result.metadata.output_tokens or 0
    return {
        "model": model_id or adapter_name,
        "mapping_accuracy": (
            sum(r["mapping_correct"] for r in scored)
            / max(1, sum(r["mapping_total"] for r in scored))
        ),
        "option_accuracy": (
            sum(r["option_correct"] for r in scored)
            / max(1, sum(r["option_total"] for r in scored))
        ),
        "unsafe_mappings": sum(r["unsafe_mappings"] for r in scored),
        "missed_clarifications": sum(r["missed_clarification"] for r in scored),
        "schema_failures": schema_failures,
        "median_latency_ms": int(statistics.median(latencies)) if latencies else 0,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", nargs="*", default=[], help="Bedrock model/inference-profile ids"
    )
    args = parser.parse_args()

    rows = [evaluate_model("fake", None)]
    for model_id in args.models:
        rows.append(evaluate_model("bedrock", model_id))

    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "region": os.environ.get("AWS_REGION", "ap-south-1"),
        "models": rows,
    }
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"model-comparison-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")

    # Print a compact comparison table.
    cols = [
        "model",
        "mapping_accuracy",
        "option_accuracy",
        "unsafe_mappings",
        "schema_failures",
        "median_latency_ms",
        "output_tokens",
    ]
    print(" | ".join(c for c in cols))
    for row in rows:
        print(
            " | ".join(f"{row[c]:.2f}" if isinstance(row[c], float) else str(row[c]) for c in cols)
        )
    print(f"\nreport: {path}")


if __name__ == "__main__":
    main()
