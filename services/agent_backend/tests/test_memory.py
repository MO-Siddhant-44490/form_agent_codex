"""Episodic mapping memory: value-free signatures and recall/remember."""

from agent_backend.memory import (
    InMemoryMappingMemory,
    field_signature,
    site_key,
)
from form_contracts import FormField, TargetDescriptor


def _field(**overrides) -> FormField:
    base = dict(
        field_id="district",
        target=TargetDescriptor(field_id="district", role="combobox", name_attr="District"),
        input_type="combobox",
        label="District",
    )
    base.update(overrides)
    return FormField.model_validate(base)


def test_site_key_normalizes_origin():
    assert site_key("https://Example.test/") == "https://example.test"
    assert site_key("https://example.test") == "https://example.test"


def test_signature_is_stable_for_the_same_identity():
    assert field_signature(_field()) == field_signature(_field())


def test_signature_ignores_values_and_options():
    # Same identity, different value/current_value/option set -> same signature.
    a = _field(current_value=None, options=None, option_labels=None)
    b = _field(
        current_value="Thane",
        options=["0", "476"],
        option_labels=["--Select--", "Thane"],
    )
    assert field_signature(a) == field_signature(b)


def test_signature_differs_on_identity():
    base = _field()
    diff_name = _field(target=TargetDescriptor(field_id="x", role="combobox", name_attr="State"))
    diff_type = _field(input_type="text")
    diff_label = _field(label="Taluka")
    assert field_signature(base) != field_signature(diff_name)
    assert field_signature(base) != field_signature(diff_type)
    assert field_signature(base) != field_signature(diff_label)


def test_in_memory_recall_after_remember():
    mem = InMemoryMappingMemory()
    site = site_key("https://pgportal.gov.in")
    sig = field_signature(_field())
    assert mem.recall(site, sig) is None
    mem.remember(site, sig, "district", "combobox")
    assert mem.recall(site, sig) == "district"
    # A different site never shares the memory.
    assert mem.recall(site_key("https://other.test"), sig) is None
