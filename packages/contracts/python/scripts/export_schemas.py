"""Export JSON Schema from the Pydantic contracts (source of truth).

Writes one deterministic schema file per exported model into
packages/contracts/schema/. Run after any contract change; CI fails on drift
(check_drift.sh).
"""

import json
import re
from pathlib import Path

from form_contracts import EXPORTED_MODELS, PROTOCOL_VERSION

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for model in EXPORTED_MODELS:
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["x-protocol-version"] = PROTOCOL_VERSION
        path = SCHEMA_DIR / f"{snake_case(model.__name__)}.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path.name)
    manifest = {"protocol_version": PROTOCOL_VERSION, "schemas": sorted(written)}
    (SCHEMA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(written)} schemas + manifest to {SCHEMA_DIR}")


if __name__ == "__main__":
    main()
