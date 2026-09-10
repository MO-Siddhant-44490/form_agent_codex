"""VLM document extraction: read a document with the multimodal model and return
a structured profile. Unlike the Textract + label-recognizer path, this is
layout-agnostic — the model reads the whole document (tables, handwriting,
abbreviations), resolves ambiguity (a person's name vs a guardian's, the primary
address), and can target the fields the current form actually needs.

Model output is DATA, re-validated here: credential-like keys are dropped and the
result is normalized to {key, value} pairs. Nothing is invented — the model is
told to omit anything not clearly present.
"""

import json
from types import SimpleNamespace

from ..model_gateway.base import ModelUnavailable
from ..model_gateway.json_chat import extract_json
from ..safety import is_credential_key

SYSTEM = """You extract a person's profile from an uploaded document (an image or
a PDF). Read the WHOLE document — understand its layout, tables, stamps,
handwriting, and abbreviations. Reason about what each value means and resolve
ambiguity from context: distinguish the person's own name from a father's,
guardian's, or spouse's name; pick the primary/current address over an old one;
map abbreviated or non-standard labels to the right field.

Return ONLY a JSON object: {"facts": [{"key": <snake_case field name>, "value":
<string>}]}.

Rules:
- Include a field ONLY if its value is clearly present in the document, OR can
  be inferred with high confidence from unambiguous evidence (e.g. country =
  "India" from nationality "Indian"; state from a well-known city). Never guess
  beyond such clear inferences; omit anything uncertain.
- Prefer these standard keys when the information is present: full_name,
  first_name, last_name, gender, date_of_birth, email, mobile, phone, address,
  sub_locality, locality, city, district, state, country, pincode, nationality,
  occupation, employer, marital_status.
- Normalize obvious formats (a date to YYYY-MM-DD, trim stray spaces) but keep
  the person's data faithful.
- Never extract passwords, OTPs, CAPTCHA text, CVVs, or full card numbers."""


def build_prompt(target_fields: list[str] | None) -> str:
    if target_fields:
        wanted = ", ".join(sorted({t for t in target_fields if t}))
        return (
            "The form being filled asks for these fields (extract their values "
            f"if the document has them): {wanted}. Also include any other clearly "
            "present standard profile fields. Return the JSON object."
        )
    return "Extract the person's profile fields present in the document. Return the JSON object."


def parse_extraction(raw: str) -> list[SimpleNamespace]:
    data = json.loads(extract_json(raw))
    out: list[SimpleNamespace] = []
    seen: set[str] = set()
    for item in data.get("facts", []):
        if not isinstance(item, dict):
            continue
        key, value = item.get("key"), item.get("value")
        if not key or value is None:
            continue
        key = str(key).strip().lower().replace(" ", "_")
        value = str(value).strip()
        if not key or not value or key in seen or is_credential_key(key):
            continue
        seen.add(key)
        out.append(SimpleNamespace(key=key, value=value))
    return out


def vlm_extract(
    gateway, doc_bytes: bytes, mime: str, target_fields: list[str] | None = None
) -> list[SimpleNamespace]:
    """Extract a profile from the document via the multimodal model. Raises
    ModelUnavailable / ValueError (unsupported type) so the caller can fall back
    to the Textract pipeline."""
    raw = gateway.extract_document(SYSTEM, build_prompt(target_fields), doc_bytes, mime)
    return parse_extraction(raw)


def supports_vlm(gateway) -> bool:
    return gateway is not None and hasattr(gateway, "extract_document")


__all__ = ["vlm_extract", "parse_extraction", "build_prompt", "supports_vlm", "ModelUnavailable"]
