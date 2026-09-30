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

    edit = apply_edits(
        transport, [_fact("full_name", "Ada Lovelace"), _fact("city", "Paris")], {"city"}
    )

    assert edit.filled_fields == ["city"]
    assert city.value == "Paris"  # changed
    assert name.value == "Ada Lovelace"  # untouched
    assert transport.execution_counts == {"city": 1}  # only one action dispatched


def test_apply_edits_reports_a_value_that_matches_no_option():
    # A typo'd / invalid value for an option field is reported honestly (with the
    # choices) rather than silently claiming success.
    sex = FakeField("sex", "select-one", "gender", "Sex", options=["Male", "Female"], value="Male")
    transport = FakeTransport(fields=[sex])

    edit = apply_edits(transport, [_fact("gender", "Xyz")], {"gender"})

    assert edit.filled_fields == []
    assert transport.execution_counts == {}  # nothing dispatched
    assert len(edit.unresolved) == 1
    assert edit.unresolved[0]["key"] == "gender"
    assert edit.unresolved[0]["value"] == "Xyz"
    assert edit.unresolved[0]["options"] == ["Male", "Female"]


def test_apply_edits_skips_a_field_already_at_the_target_value():
    city = FakeField("city", "text", "city", "City", value="Paris", required=True)
    transport = FakeTransport(fields=[city])

    edit = apply_edits(transport, [_fact("city", "Paris")], {"city"})

    assert edit.filled_fields == ["city"]  # reported satisfied
    assert transport.execution_counts == {}  # nothing dispatched — already correct


def test_back_to_back_driver_calls_continue_the_sequence():
    """A fill, then auto-repair, then continue-after-answer all run on ONE
    connection: the second call must not restart numbering (the extension's
    replay guard would reject every action as stale)."""
    from agent_backend.driver import run_fill

    name = FakeField("full-name", "text", "full_name", "Full name", required=True)
    city = FakeField("city", "text", "city", "City", required=True)
    transport = FakeTransport(fields=[name, city])
    run_fill(transport, [_fact("full_name", "Ada Lovelace"), _fact("city", "London")])
    first_high = transport.last_action_seq
    assert first_high >= 2

    edit = apply_edits(
        transport, [_fact("full_name", "Ada Lovelace"), _fact("city", "Paris")], {"city"}
    )
    assert edit.filled_fields == ["city"] and edit.failed_fields == []
    assert city.value == "Paris"
    assert transport.last_action_seq > first_high  # continued, not restarted
