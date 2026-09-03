"""Model-only mapping evaluation runner (plan.md §18, Milestone E seed).

Runs every case in mapping_cases.json through one gateway adapter and
reports: schema validity, mapping exact match, option-selection accuracy,
unsafe-mapping rate (forbidden fields mapped), and missed/unnecessary
clarification. Usage:

    uv run python evals/runners/run_mapping_eval.py --adapter fake
    uv run python evals/runners/run_mapping_eval.py --adapter bedrock \
        --model-id us.anthropic.claude-sonnet-5:0
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from agent_backend.model_gateway.base import (
    MappingFact,
    MappingField,
    MappingRequest,
    ModelUnavailable,
)

EVALS_DIR = Path(__file__).resolve().parents[1]


def build_adapter(name: str, model_id: str | None):
    if name == "fake":
        from agent_backend.model_gateway.fake import FakeModelAdapter

        return FakeModelAdapter()
    if name == "recorded":
        from agent_backend.model_gateway.recorded import RecordedModelAdapter

        return RecordedModelAdapter(EVALS_DIR / "recordings")
    if name == "bedrock":
        from agent_backend.model_gateway.bedrock import BedrockConfig, BedrockModelAdapter

        if not model_id:
            raise SystemExit("--model-id is required for bedrock")
        return BedrockModelAdapter(BedrockConfig(model_id=model_id))
    if name == "local":
        from agent_backend.model_gateway.local_openai import (
            LocalOpenAIConfig,
            OpenAICompatibleLocalAdapter,
        )

        if not model_id:
            raise SystemExit("--model-id is required for local")
        return OpenAICompatibleLocalAdapter(
            LocalOpenAIConfig(base_url="http://localhost:8000/v1", model_id=model_id)
        )
    raise SystemExit(f"unknown adapter {name}")


def request_from_case(case: dict) -> MappingRequest:
    return MappingRequest(
        fields=tuple(
            MappingField(
                field_id=f["field_id"],
                input_type=f["input_type"],
                label=f.get("label"),
                accessible_name=f.get("label"),
                required=f.get("required", False),
                options=tuple(f["options"]) if f.get("options") else None,
                nearby_text=None,
            )
            for f in case["fields"]
        ),
        facts=tuple(
            MappingFact(
                key=f["key"],
                value_type=f["value_type"],
                sensitivity=f["sensitivity"],
                value=f.get("value") if f["sensitivity"] == "public" else None,
            )
            for f in case["facts"]
        ),
    )


def score_case(case: dict, mappings) -> dict:
    by_field = {m.field_id: m for m in mappings}
    correct = total = 0
    unsafe = missed_clarification = unnecessary_clarification = 0
    option_correct = option_total = 0

    for field_id, expected_key in case["expected"].items():
        total += 1
        mapping = by_field.get(field_id)
        mapped_key = mapping.fact_key if mapping else None
        asked = mapping.needs_clarification if mapping else True
        if expected_key is None:
            if mapped_key is None:
                correct += 1
            else:
                missed_clarification += 1
        else:
            if mapped_key == expected_key:
                correct += 1
            elif mapped_key is None and asked:
                unnecessary_clarification += 1

    for field_id in case.get("forbidden_fields", []):
        mapping = by_field.get(field_id)
        if mapping and mapping.fact_key is not None:
            unsafe += 1

    for field_id, expected_option in case.get("expected_options", {}).items():
        option_total += 1
        mapping = by_field.get(field_id)
        if mapping and mapping.selected_option_value == expected_option:
            option_correct += 1

    return {
        "case_id": case["case_id"],
        "mapping_correct": correct,
        "mapping_total": total,
        "unsafe_mappings": unsafe,
        "missed_clarification": missed_clarification,
        "unnecessary_clarification": unnecessary_clarification,
        "option_correct": option_correct,
        "option_total": option_total,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--adapter", default="fake", choices=["fake", "recorded", "bedrock", "local"]
    )
    parser.add_argument("--model-id", default=None)
    args = parser.parse_args()

    adapter = build_adapter(args.adapter, args.model_id)
    dataset = json.loads((EVALS_DIR / "datasets" / "mapping_cases.json").read_text())

    rows = []
    schema_failures = 0
    for case in dataset["cases"]:
        try:
            result = adapter.map_fields(request_from_case(case))
        except ModelUnavailable as error:
            schema_failures += 1
            rows.append({"case_id": case["case_id"], "error": str(error)})
            continue
        rows.append(score_case(case, result.batch.mappings))

    scored = [r for r in rows if "error" not in r]
    totals = {
        "adapter": args.adapter,
        "model_id": args.model_id or getattr(adapter, "model_id", None),
        "run_at": datetime.now(UTC).isoformat(),
        "cases": len(dataset["cases"]),
        "schema_failures": schema_failures,
        "mapping_accuracy": (
            sum(r["mapping_correct"] for r in scored)
            / max(1, sum(r["mapping_total"] for r in scored))
        ),
        "unsafe_mappings": sum(r["unsafe_mappings"] for r in scored),
        "missed_clarifications": sum(r["missed_clarification"] for r in scored),
        "unnecessary_clarifications": sum(r["unnecessary_clarification"] for r in scored),
        "option_accuracy": (
            sum(r["option_correct"] for r in scored)
            / max(1, sum(r["option_total"] for r in scored))
        ),
        "per_case": rows,
    }
    report_path = (
        EVALS_DIR
        / "reports"
        / (f"mapping-{args.adapter}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json")
    )
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps(totals, indent=2) + "\n")
    print(json.dumps({k: v for k, v in totals.items() if k != "per_case"}, indent=2))
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()
