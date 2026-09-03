"""Recorded adapter (plan.md Module 6): replays JSON recordings keyed by the
request fingerprint for deterministic integration tests without model cost.
RecordingWrapper captures a live adapter's responses into the same format."""

import json
from pathlib import Path

from form_contracts import FieldMappingBatch, ModelCallMetadata

from .base import GatewayResult, MappingRequest, ModelGateway, ModelUnavailable


class RecordedModelAdapter:
    model_id = "recorded"

    def __init__(self, recordings_dir: Path) -> None:
        self._dir = recordings_dir

    def map_fields(self, request: MappingRequest) -> GatewayResult:
        path = self._dir / f"map_fields-{request.fingerprint()}.json"
        if not path.exists():
            raise ModelUnavailable(f"no recording for fingerprint {request.fingerprint()}")
        data = json.loads(path.read_text())
        return GatewayResult(
            batch=FieldMappingBatch.model_validate(data["batch"]),
            metadata=ModelCallMetadata.model_validate(data["metadata"]),
        )


class RecordingWrapper:
    """Wraps a live adapter and saves every response as a recording."""

    def __init__(self, inner: ModelGateway, recordings_dir: Path) -> None:
        self._inner = inner
        self._dir = recordings_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def map_fields(self, request: MappingRequest) -> GatewayResult:
        result = self._inner.map_fields(request)
        path = self._dir / f"map_fields-{request.fingerprint()}.json"
        path.write_text(
            json.dumps(
                {
                    "batch": result.batch.model_dump(mode="json"),
                    "metadata": result.metadata.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return result
