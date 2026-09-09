"""Live form-state snapshot: value-aware filled detection and delta summary."""

from agent_backend.snapshot import build_snapshot, is_filled, summarize
from agent_backend.transports.fake import FakeTransport
from form_contracts import FormField, TargetDescriptor


def _field(**overrides) -> FormField:
    base = dict(
        field_id="f",
        target=TargetDescriptor(field_id="f", role="textbox"),
        input_type="text",
    )
    base.update(overrides)
    return FormField.model_validate(base)


def test_is_filled_text():
    assert is_filled(_field(current_value="Asha")) is True
    assert is_filled(_field(current_value=None)) is False
    assert is_filled(_field(current_value="")) is False


def test_is_filled_treats_dropdown_placeholder_as_empty():
    placeholder = _field(
        input_type="combobox",
        current_value="0",
        options=["0", "476"],
        option_labels=["--Select one--", "Thane"],
    )
    real = _field(
        input_type="combobox",
        current_value="476",
        options=["0", "476"],
        option_labels=["--Select one--", "Thane"],
    )
    assert is_filled(placeholder) is False
    assert is_filled(real) is True


def test_is_filled_checkbox_uses_checked():
    assert is_filled(_field(input_type="checkbox", checked=True)) is True
    assert is_filled(_field(input_type="checkbox", checked=False)) is False


def test_build_snapshot_covers_visible_fields():
    snap = build_snapshot(FakeTransport().observe())
    assert len(snap) >= 1
    entry = snap[0]
    assert {"field_id", "label", "filled", "value", "required", "error"} <= set(entry)


def test_summarize_reports_new_fields_and_empty_required_and_errors():
    prev = [{"field_id": "country", "label": "Country", "required": True, "filled": True, "error": None}]
    now = [
        {"field_id": "country", "label": "Country", "required": True, "filled": True, "error": None},
        {"field_id": "state", "label": "State", "required": True, "filled": False, "error": None},
        {"field_id": "addr", "label": "Address", "required": True, "filled": True,
         "error": "must be under 50 characters"},
    ]
    s = summarize(now, prev)
    assert [f["field_id"] for f in s["new_fields"]] == ["state", "addr"]
    assert [f["field_id"] for f in s["empty_required"]] == ["state"]
    assert [f["field_id"] for f in s["errors"]] == ["addr"]
    assert "State" in s["text"] and "under 50 characters" in s["text"]


def test_summarize_no_previous_has_no_new_fields():
    now = [{"field_id": "a", "label": "A", "required": False, "filled": True, "error": None}]
    assert summarize(now, None)["new_fields"] == []
