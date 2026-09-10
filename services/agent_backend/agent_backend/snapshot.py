"""Live form-state perception for the chat layer. A snapshot is a compact,
value-aware view of the form as it stands right now — what is filled, what is
empty, what changed since the last turn, and what the site is complaining about.
The reasoning chat layer works from THIS, so the agent is always aware of the
form's current state instead of acting blind (plan.md: complete perception, not
deterministic re-fills)."""

from form_contracts import FieldPurpose, FormField, PageObservation

_NON_FIELD = frozenset({"submit", "button", "reset", "image"})


def _selected_label(field: FormField) -> str | None:
    v = field.current_value
    if v is None or not field.options or not field.option_labels:
        return None
    try:
        return field.option_labels[field.options.index(v)]
    except (ValueError, IndexError):
        return None


def _is_placeholder(label: str) -> bool:
    low = label.strip().lower()
    return low == "" or low.startswith("--") or "select" in low or low in ("none", "choose")


def is_filled(field: FormField) -> bool:
    """A field counts as filled only if it holds a real value — a dropdown left
    on its '--Select one--' placeholder is empty, not filled."""
    if field.input_type in ("checkbox", "radio"):
        return field.checked is True
    if field.value_redacted:
        return bool(field.value_length)
    value = field.current_value
    if not value:
        return False
    label = _selected_label(field)
    if label is not None and _is_placeholder(label):
        return False
    return True


def _display_value(field: FormField) -> str | None:
    if field.value_redacted:
        return None
    label = _selected_label(field)
    if label is not None and not _is_placeholder(label):
        return label
    return field.current_value


def build_snapshot(observation: PageObservation) -> list[dict]:
    """A value-aware, JSON-friendly view of every actionable field."""
    fields: list[dict] = []
    for f in observation.fields:
        if not f.visible or f.input_type in _NON_FIELD:
            continue
        fields.append(
            {
                "field_id": f.field_id,
                "label": f.label or f.accessible_name or f.field_id,
                "input_type": f.input_type,
                "required": f.required,
                "filled": is_filled(f),
                "value": _display_value(f),
                "error": f.validation_message,
                # The human's alone: a credential or captcha ("complete it on
                # the page"); a consent needs their explicit yes.
                "human_only": f.purpose in (FieldPurpose.CREDENTIAL, FieldPurpose.CAPTCHA),
                "consent": f.purpose is FieldPurpose.CONSENT,
                "purpose": f.purpose.value,
                "max_length": f.max_length,
                "options": list(f.options) if f.options else None,
                "option_labels": list(f.option_labels) if f.option_labels else None,
            }
        )
    return fields


def summarize(snapshot: list[dict], previous: list[dict] | None = None) -> dict:
    """A delta-aware summary the panel shows and the interpreter reasons over:
    counts, fields that just appeared, fields still needing a value, and site
    validation errors."""
    prev_ids = {f["field_id"] for f in previous} if previous else set()
    new_fields = [f for f in snapshot if previous is not None and f["field_id"] not in prev_ids]
    empty_required = [f for f in snapshot if f["required"] and not f["filled"]]
    errors = [f for f in snapshot if f["error"]]
    filled = [f for f in snapshot if f["filled"]]
    needs_value = [f for f in empty_required if not f.get("human_only") and not f.get("consent")]
    yours = [f for f in empty_required if f.get("human_only")]
    consents = [f for f in snapshot if f.get("consent") and not f["filled"]]

    parts = [f"{len(filled)} of {len(snapshot)} field(s) filled."]
    if new_fields:
        parts.append("Just appeared: " + ", ".join(f["label"] for f in new_fields) + ".")
    if needs_value:
        parts.append("Still needs a value: " + ", ".join(f["label"] for f in needs_value) + ".")
    if consents:
        parts.append("Needs your decision: " + ", ".join(f["label"] for f in consents) + ".")
    if yours:
        parts.append("Yours to complete on the page: " + ", ".join(f["label"] for f in yours) + ".")
    if errors:
        parts.append("Issues: " + "; ".join(f"{f['label']} — {f['error']}" for f in errors) + ".")
    return {
        "text": " ".join(parts),
        "new_fields": new_fields,
        "empty_required": empty_required,
        "errors": errors,
    }
