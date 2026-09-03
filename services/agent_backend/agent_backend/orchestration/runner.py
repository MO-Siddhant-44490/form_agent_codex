"""Convenience wrapper around the compiled graph: start a run, inspect
interrupts, and resume with human input. Thread id == run id, so the
checkpointer holds one durable timeline per run."""

from dataclasses import dataclass
from typing import Any

from form_contracts import DocumentFact, TargetDescriptor
from langgraph.types import Command


@dataclass
class RunHandle:
    run_id: str
    state: dict
    interrupt: dict | None  # the interrupt payload when paused for a human


def _extract_interrupt(result: dict) -> dict | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def start_run(
    graph: Any,
    run_id: str,
    facts: list[DocumentFact],
    *,
    goal: str = "fill_only",
    submit_target: TargetDescriptor | None = None,
) -> RunHandle:
    config = {"configurable": {"thread_id": run_id}}
    initial = {
        "run_id": run_id,
        "goal": goal,
        "facts": facts,
        "submit_target": submit_target,
    }
    result = graph.invoke(initial, config)
    return _handle(graph, run_id, result)


def resume_run(graph: Any, run_id: str, resume_value: Any) -> RunHandle:
    """Resume a run. Pass the human's answer to a pending interrupt as
    resume_value; pass None to simply continue after a crash (there is no
    interrupt to answer — LangGraph replays from the last checkpoint)."""
    config = {"configurable": {"thread_id": run_id}}
    command = None if resume_value is None else Command(resume=resume_value)
    result = graph.invoke(command, config)
    return _handle(graph, run_id, result)


def _handle(graph: Any, run_id: str, result: dict) -> RunHandle:
    config = {"configurable": {"thread_id": run_id}}
    snapshot = graph.get_state(config)
    return RunHandle(
        run_id=run_id,
        state=snapshot.values,
        interrupt=_extract_interrupt(result),
    )
