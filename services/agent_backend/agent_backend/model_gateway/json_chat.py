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
