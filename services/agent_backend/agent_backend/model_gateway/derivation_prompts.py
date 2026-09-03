"""Prompt for the derivation stage: compute/infer values a form needs that are
not directly present, from already-extracted document facts. Output is
schema-constrained and re-validated against the provided source ids."""

import json

from .base import DerivationRequest

DERIVATION_SYSTEM = """You compute or infer values a web form needs, using ONLY the
facts provided. You never invent data.

Rules:
- Use only AVAILABLE_FACTS. Reference each source you use by its exact fact_id.
- Compute values (age from a date of birth using TODAY, sums of line items,
  years between dates, unit conversions). Show the operation.
- If a target cannot be computed from the available facts, omit it. Do not
  guess.
- Respond with ONLY JSON: {"derived": [{"key": str, "value": str,
  "value_type": str, "operation": str, "source_fact_ids": [str, ...],
  "explanation": str, "confidence": number 0..1}]}"""


def build_derivation_prompt(request: DerivationRequest) -> str:
    available = [
        {"fact_id": a.fact_id, "key": a.key, "value": a.value, "value_type": a.value_type}
        for a in request.available
    ]
    targets = [
        {"key": t.key, "value_type": t.value_type, "description": t.description}
        for t in request.targets
    ]
    return (
        f"TODAY: {request.today}\n\n"
        f"AVAILABLE_FACTS:\n{json.dumps(available, indent=1)}\n\n"
        f"TARGETS (values the form needs):\n{json.dumps(targets, indent=1)}\n\n"
        "Compute what you can. JSON only."
    )
