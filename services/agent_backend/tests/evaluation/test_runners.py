"""Smoke tests for the offline eval runners: they produce well-formed reports
and enforce their safety gates."""

import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def run_script(name: str, argv: list[str]):
    path = REPO / "evals" / "runners" / name
    old = sys.argv
    sys.argv = [str(path), *argv]
    try:
        runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.argv = old


def test_shadow_eval_runner_offline():
    run_script("run_shadow_eval.py", [])  # its safety asserts run inside


def test_ablation_runner_offline():
    run_script("run_ablation.py", [])


def test_benchmark_runner_offline():
    run_script("run_benchmark.py", [])


def test_model_comparison_offline_mechanics():
    run_script("run_model_comparison.py", [])  # fake-only, no live creds
