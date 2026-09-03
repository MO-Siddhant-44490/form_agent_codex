"""Shared core for chat-style providers: prompt in, JSON out, schema
validation with one bounded retry, classified failure otherwise
(plan.md §11.2)."""

import json
import re
import time
from collections.abc import Callable

from form_contracts import FieldMappingBatch, ModelCallMetadata
from pydantic import ValidationError

from .base import GatewayResult, MappingRequest, ModelUnavailable
from .prompts import SYSTEM_PROMPT, build_user_prompt

# One retry on schema-invalid output; anything more burns budget for noise.
MAX_ATTEMPTS = 2

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> str:
    match = _JSON_BLOCK.search(text)
    if match is None:
        raise ValueError("no JSON object in model output")
    return match.group(0)


def run_mapping_chat(
    request: MappingRequest,
    model_id: str,
    complete: Callable[[str, str], tuple[str, int | None, int | None]],
) -> GatewayResult:
    """`complete(system, user) -> (text, input_tokens, output_tokens)`."""
    user_prompt = build_user_prompt(request)
    start = time.monotonic()
    attempts = 0
    last_error: Exception | None = None
    input_tokens = output_tokens = None
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        text, input_tokens, output_tokens = complete(SYSTEM_PROMPT, user_prompt)
        try:
            batch = FieldMappingBatch.model_validate(json.loads(extract_json(text)))
        except (ValueError, ValidationError) as error:
            last_error = error
            continue
        return GatewayResult(
            batch=batch,
            metadata=ModelCallMetadata(
                model_id=model_id,
                latency_ms=int((time.monotonic() - start) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                schema_valid=True,
                retries=attempts - 1,
                request_fingerprint=request.fingerprint(),
            ),
        )
    raise ModelUnavailable(f"schema-invalid output after {attempts} attempts: {last_error}")


def run_derivation_chat(
    request: "DerivationRequest",
    model_id: str,
    complete: Callable[[str, str], tuple[str, int | None, int | None]],
) -> "DerivationResult":
    """Run the derivation call: prompt in, JSON out, validate each proposed
    derived fact against the provided source ids before accepting it. A
    proposal citing an unknown source id, or with no sources, is discarded
    (the model must not invent data — same defensive stance as mapping)."""
    import time

    from form_contracts import Derivation, DocumentFact, FactStatus

    from .base import DerivationResult
    from .derivation_prompts import DERIVATION_SYSTEM, build_derivation_prompt

    user_prompt = build_derivation_prompt(request)
    valid_ids = {a.fact_id for a in request.available}
    key_types = {t.key: t.value_type for t in request.targets}
    start = time.monotonic()
    attempts = 0
    last_error: Exception | None = None
    input_tokens = output_tokens = None

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        text, input_tokens, output_tokens = complete(DERIVATION_SYSTEM, user_prompt)
        try:
            payload = json.loads(extract_json(text))
            proposals = payload["derived"]
        except (ValueError, KeyError) as error:
            last_error = error
            continue

        facts: list[DocumentFact] = []
        for prop in proposals:
            source_ids = [sid for sid in prop.get("source_fact_ids", []) if sid in valid_ids]
            if not source_ids:
                continue  # no valid source -> discard (never invent data)
            key = prop.get("key")
            if key not in key_types:
                continue  # not a requested target -> discard
            try:
                facts.append(
                    DocumentFact(
                        fact_id=f"derived-{key}-{request.fingerprint()}",
                        key=key,
                        value=str(prop["value"]),
                        value_type=key_types[key],
                        confidence=float(prop.get("confidence", 0.0)),
                        sensitivity="personal",
                        status=FactStatus.DERIVED,
                        derivation=Derivation(
                            operation=str(prop.get("operation", "unknown")),
                            source_fact_ids=source_ids,
                            explanation=str(prop.get("explanation", "")),
                        ),
                    )
                )
            except Exception as error:  # schema/value error on one fact
                last_error = error
                continue

        return DerivationResult(
            facts=facts,
            metadata=ModelCallMetadata(
                model_id=model_id,
                latency_ms=int((time.monotonic() - start) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                schema_valid=True,
                retries=attempts - 1,
                request_fingerprint=request.fingerprint(),
            ),
        )
    raise ModelUnavailable(f"derivation output invalid after {attempts} attempts: {last_error}")
