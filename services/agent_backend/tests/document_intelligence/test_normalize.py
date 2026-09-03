import pytest
from agent_backend.document_intelligence.normalize import (
    normalize_date,
    normalize_email,
    normalize_number,
    normalize_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1998-04-17", "1998-04-17"),
        ("17/04/1998", "1998-04-17"),
        ("17-04-1998", "1998-04-17"),
        ("17 Apr 1998", "1998-04-17"),
        ("17 APR 1998", "1998-04-17"),
        ("17 April 1998", "1998-04-17"),
        ("April 17, 1998", "1998-04-17"),
        ("Apr 17 1998", "1998-04-17"),
    ],
)
def test_date_formats(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/1998", "not a date", "17", "99/99/9999"])
def test_unparseable_dates_abstain(raw):
    assert normalize_date(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+1 (555) 010-2030", "+15550102030"),
        ("555 010 2030", "5550102030"),
        ("+91-98765-43210", "+919876543210"),
    ],
)
def test_phone_formats(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12", "call me", "+1 555 CALL NOW"])
def test_unparseable_phones_abstain(raw):
    assert normalize_phone(raw) is None


def test_email_lowercased_and_validated():
    assert normalize_email("Ada@Example.test") == "ada@example.test"
    assert normalize_email("not-an-email") is None


def test_number_normalization():
    assert normalize_number("5") == "5"
    assert normalize_number("1,200") == "1200"
    assert normalize_number("5.5") == "5.5"
    assert normalize_number("five") is None
