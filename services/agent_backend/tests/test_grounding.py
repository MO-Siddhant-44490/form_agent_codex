"""Structural (autocomplete) grounding: bind a field to a fact by its W3C
autocomplete token, deterministically and value-free (plan.md insight #2)."""

from agent_backend.grounding import autocomplete_fact
from form_contracts import (
    DocumentFact,
    FactStatus,
    FactValueType,
    FormField,
    Sensitivity,
    TargetDescriptor,
)


def _field(autocomplete: str | None, input_type: str = "text") -> FormField:
    return FormField.model_validate(
        dict(
            field_id="f",
            target=TargetDescriptor(field_id="f", role="textbox", autocomplete=autocomplete),
            input_type=input_type,
        )
    )


def _fact(key: str, value_type: FactValueType = FactValueType.STRING) -> DocumentFact:
    return DocumentFact(
        fact_id=f"f-{key}",
        key=key,
        value=f"{key}-value",
        value_type=value_type,
        confidence=1.0,
        sensitivity=Sensitivity.PERSONAL,
        status=FactStatus.USER_PROVIDED,
    )


def _facts(*facts: DocumentFact) -> dict[str, DocumentFact]:
    return {f.key: f for f in facts}


def test_email_token_maps_to_email_fact():
    facts = _facts(_fact("full_name"), _fact("email", FactValueType.EMAIL))
    assert autocomplete_fact(_field("email"), facts).key == "email"


def test_tel_token_maps_by_value_type_when_key_differs():
    facts = _facts(_fact("contact_number", FactValueType.PHONE))
    assert autocomplete_fact(_field("tel"), facts).key == "contact_number"


def test_given_and_family_name_tokens():
    facts = _facts(_fact("first_name"), _fact("last_name"))
    assert autocomplete_fact(_field("given-name"), facts).key == "first_name"
    assert autocomplete_fact(_field("family-name"), facts).key == "last_name"


def test_prefix_and_section_are_stripped():
    facts = _facts(_fact("address"))
    assert autocomplete_fact(_field("shipping street-address"), facts).key == "address"
    assert autocomplete_fact(_field("section-ship billing street-address"), facts).key == "address"


def test_postal_and_country_and_state_tokens():
    facts = _facts(_fact("pincode"), _fact("country"), _fact("state"))
    assert autocomplete_fact(_field("postal-code"), facts).key == "pincode"
    assert autocomplete_fact(_field("country-name"), facts).key == "country"
    assert autocomplete_fact(_field("address-level1"), facts).key == "state"


def test_credential_tokens_never_ground():
    facts = _facts(_fact("password"), _fact("card", FactValueType.STRING))
    assert autocomplete_fact(_field("new-password"), facts) is None
    assert autocomplete_fact(_field("cc-number"), facts) is None


def test_unknown_or_absent_token_returns_none():
    facts = _facts(_fact("email", FactValueType.EMAIL))
    assert autocomplete_fact(_field("nope-token"), facts) is None
    assert autocomplete_fact(_field(None), facts) is None


def test_no_matching_fact_returns_none():
    facts = _facts(_fact("full_name"))
    assert autocomplete_fact(_field("email"), facts) is None
