"""Ablation study runner (plan.md §17). Runs every scenario under every config
and reports the grid plus the headline false-success rates. Offline and
deterministic (fake transport + fake model)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_backend.evaluation.ablation import false_success_rate, run_ablation

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def main() -> None:
    cells = run_ablation()
    grid = [
        {
            "scenario": c.scenario,
            "config": c.config,
            "outcome": c.outcome,
            "filled": c.filled_count,
            "truly_correct": c.truly_correct,
        }
        for c in cells
    ]
    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "false_success_rate": false_success_rate(cells),
        "grid": grid,
    }
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"ablation-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"false_success_rate": report["false_success_rate"]}, indent=2))
    print(f"\nfull grid: {path}")


if __name__ == "__main__":
    main()
