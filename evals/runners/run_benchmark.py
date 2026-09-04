"""Consolidated benchmark report (plan.md §16-18): runs the offline evaluation
suite — shadow-mode metrics on controlled pages and the ablation study — and
emits a single paper-grade report (JSON + Markdown). Deterministic; live model
comparison is a separate runner (run_model_comparison.py)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_backend.evaluation.ablation import false_success_rate, run_ablation
from agent_backend.evaluation.metrics import Aggregate, GroundTruth, score_plan
from agent_backend.evaluation.shadow import propose_plan
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeTransport

REPORTS = Path(__file__).resolve().parents[1] / "reports"

SHADOW_TRUTH = GroundTruth(
    fixture_id="basic-form",
    expected_fill={
        "full-name": "Ada Lovelace",
        "email": "ada@example.test",
        "dob": "1998-04-17",
        "country": "IN",
        "experience": "5",
    },
)


def shadow_section() -> dict:
    agg = Aggregate()
    agg.add(score_plan(propose_plan(FakeTransport(), slice1_facts()), SHADOW_TRUTH))
    return agg.summary()


def ablation_section() -> dict:
    cells = run_ablation()
    return {
        "false_success_rate": false_success_rate(cells),
        "grid": [
            {
                "scenario": c.scenario,
                "config": c.config,
                "outcome": c.outcome,
                "truly_correct": c.truly_correct,
            }
            for c in cells
        ],
    }


def to_markdown(report: dict) -> str:
    s = report["shadow"]
    lines = [
        "# FormBench evaluation report",
        f"\nGenerated {report['run_at']}\n",
        "## Shadow mode (propose-only, no side effects)",
        f"- Field-detection recall: {s['field_detection_recall']:.2f}",
        f"- Mapping accuracy: {s['mapping_accuracy']:.2f}",
        f"- Unsafe proposals: {s['unsafe_proposals']} (target 0)",
        f"- SUBMIT proposed in shadow mode: {s['submit_proposals']} (target 0)",
        "\n## Ablations (false-success rate by config)",
        "\n| Config | False-success rate |",
        "| --- | --- |",
    ]
    for cfg, rate in report["ablation"]["false_success_rate"].items():
        lines.append(f"| {cfg} | {rate:.2f} |")
    lines.append(
        "\nThe open-loop configuration reports completion on values the "
        "perceive-act-verify loop rejects — its non-zero false-success rate is "
        "the core evidence for the closed loop."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "shadow": shadow_section(),
        "ablation": ablation_section(),
    }
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (REPORTS / f"benchmark-{stamp}.json").write_text(json.dumps(report, indent=2) + "\n")
    md = to_markdown(report)
    (REPORTS / f"benchmark-{stamp}.md").write_text(md)
    print(md)
    print(f"report: {REPORTS / f'benchmark-{stamp}.json'}")


if __name__ == "__main__":
    main()
