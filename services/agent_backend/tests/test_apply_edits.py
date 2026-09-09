"""Targeted edits: change only the affected field(s), not the whole form."""

from agent_backend.driver import apply_edits
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import DocumentFact, FactStatus, FactValueType, Sensitivity


def _fact(key: str, value: str) -> DocumentFact:
    return DocumentFact(
        fact_id=f"f-{key}",
        key=key,
        value=value,
        value_type=FactValueType.STRING,
        confidence=1.0,
        sensitivity=Sensitivity.PERSONAL,
        status=FactStatus.USER_PROVIDED,
    )


def test_apply_edits_changes_only_the_targeted_field():
    # A form already filled; the user corrects one value.
    name = FakeField("full-name", "text", "full_name", "Full name", value="Ada Lovelace")
    city = FakeField("city", "text", "city", "City", value="London", required=True)
    transport = FakeTransport(fields=[name, city])

    edit = apply_edits(transport, [_fact("full_name", "Ada Lovelace"), _fact("city", "Paris")], {"city"})

    assert edit.filled_fields == ["city"]
    assert city.value == "Paris"  # changed
    assert name.value == "Ada Lovelace"  # untouched
    assert transport.execution_counts == {"city": 1}  # only one action dispatched


def test_apply_edits_skips_a_field_already_at_the_target_value():
    city = FakeField("city", "text", "city", "City", value="Paris", required=True)
    transport = FakeTransport(fields=[city])

    edit = apply_edits(transport, [_fact("city", "Paris")], {"city"})

    assert edit.filled_fields == ["city"]  # reported satisfied
    assert transport.execution_counts == {}  # nothing dispatched — already correct
