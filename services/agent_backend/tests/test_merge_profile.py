"""Profile merge (profile.merge_document): new fields added, identical values
deduped, differing values surfaced as conflicts (never silently overwritten)."""

from types import SimpleNamespace

from agent_backend.profile import merge_document as _merge_profile


def _fact(key: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(key=key, value=value)


def test_merge_adds_new_fields_and_dedups_identical():
    current = [{"key": "email", "value": "a@b.com"}, {"key": "full_name", "value": "Asha"}]
    extracted = [_fact("email", "a@b.com"), _fact("phone", "9990001112")]
    fields, conflicts = _merge_profile(current, extracted)

    values = {f["key"]: f["value"] for f in fields}
    assert values == {"email": "a@b.com", "full_name": "Asha", "phone": "9990001112"}
    assert conflicts == []


def test_merge_flags_differing_value_as_conflict():
    current = [{"key": "email", "value": "old@b.com"}]
    extracted = [_fact("email", "new@b.com")]
    fields, conflicts = _merge_profile(current, extracted)

    # The existing value is kept until the user resolves the conflict.
    assert fields == [{"key": "email", "value": "old@b.com"}]
    assert conflicts == [{"key": "email", "existing": "old@b.com", "incoming": "new@b.com"}]


def test_merge_into_empty_profile_is_a_new_profile():
    fields, conflicts = _merge_profile([], [_fact("full_name", "Asha"), _fact("email", "a@b.com")])
    assert {f["key"] for f in fields} == {"full_name", "email"}
    assert conflicts == []


def test_merge_ignores_whitespace_only_differences():
    current = [{"key": "name", "value": "Asha Rao"}]
    fields, conflicts = _merge_profile(current, [_fact("name", " Asha Rao ")])
    assert conflicts == []
    assert fields == [{"key": "name", "value": "Asha Rao"}]
