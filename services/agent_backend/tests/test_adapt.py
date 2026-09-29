"""Filling like a person: fit the value to the field before typing, and when
the site still rejects it, try another format, then a model repair guided by
the site's error, before asking the user. Optional fields are never asked."""

from agent_backend.adapt import adapt, candidates, plausible_reshape
from agent_backend.driver import apply_edits, run_fill
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import DocumentFact, FactStatus, FactValueType, RunOutcome, Sensitivity


def _fact(key, value):
    return DocumentFact(
        fact_id=f"f-{key}",
        key=key,
        value=value,
        value_type=FactValueType.STRING,
        confidence=1.0,
        sensitivity=Sensitivity.PERSONAL,
        status=FactStatus.USER_PROVIDED,
    )


def _field(**kw):
    obs = FakeTransport(
        fields=[
            FakeField(
                "f",
                kw.pop("input_type", "text"),
                kw.pop("name", "f"),
                kw.pop("label", "Field"),
                **kw,
            )
        ]
    ).observe()
    return obs.fields[0], obs


MOBILE = "+91 99401 26718"


# -- phone numbers ------------------------------------------------------------


def test_mobile_box_with_10_digit_limit_gets_the_national_number():
    f, obs = _field(input_type="tel", label="Mobile Number", max_length=10)
    assert adapt(f, MOBILE, "mobile", obs) == "9940126718"


def test_placeholder_hint_or_pattern_also_means_national_number():
    f, obs = _field(
        input_type="number", label="Mobile Number", placeholder="Enter 10-digits mobile no."
    )
    assert adapt(f, MOBILE, "mobile", obs) == "9940126718"
    f, obs = _field(input_type="text", label="Phone", pattern=r"[0-9]{10}")
    assert adapt(f, MOBILE, "mobile", obs) == "9940126718"


def test_separate_country_code_picker_means_national_number():
    t = FakeTransport(
        fields=[
            FakeField("cc", "select-one", "cc", "Country code", options=["+91", "+1"]),
            FakeField("m", "tel", "mob", "Mobile/Whatsapp Number"),
        ]
    )
    obs = t.observe()
    assert adapt(obs.fields[1], MOBILE, "mobile", obs) == "9940126718"


def test_unconstrained_phone_keeps_the_persons_own_format_first():
    f, obs = _field(input_type="tel", label="Phone")
    assert candidates(f, MOBILE, "phone", obs)[0] == MOBILE


# -- dates and lengths ----------------------------------------------------------


def test_dates_follow_the_fields_stated_format():
    f, obs = _field(label="Date of Birth (DD/MM/YYYY)")
    assert adapt(f, "1988-09-23", "date_of_birth", obs) == "23/09/1988"
    f, obs = _field(label="Date of Birth (YYYY-MM-DD)")
    assert adapt(f, "23 September 1988 (1988-09-23)", "date_of_birth", obs) == "1988-09-23"


def test_long_address_is_abbreviated_then_trimmed_at_a_word_boundary():
    f, obs = _field(label="Address", max_length=40)
    out = adapt(f, "No. 42, Second Cross Street, Kasturba Nagar, Adyar", "address", obs)
    assert len(out) <= 40
    assert out.startswith("No. 42, 2nd Cr St")
    assert not out.endswith(",")


# -- provenance of repairs --------------------------------------------------------


def test_plausible_reshape_accepts_reshapes_and_rejects_new_content():
    assert plausible_reshape(MOBILE, "9940126718")
    assert plausible_reshape("No. 42, Second Cross Street, Adyar", "No 42, 2nd Cross St, Adyar")
    assert not plausible_reshape(MOBILE, "9876543210")  # a different number
    assert not plausible_reshape("Chennai", "Mumbai")


# -- the driver: adapt, retry, repair, then ask -------------------------------------


def test_driver_types_the_adapted_value_and_counts_it_as_done():
    mob = FakeField("m", "tel", "mobile", "Mobile Number", required=True, max_length=10)
    t = FakeTransport(fields=[mob])
    result = run_fill(t, [_fact("mobile", MOBILE)])
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert mob.value == "9940126718"
    assert t.execution_counts == {"m": 1}  # typed once, not re-filled forever


def test_driver_tries_the_next_format_when_the_site_rejects_one():
    # No hint on the field, but the site only accepts 10 digits.
    mob = FakeField(
        "m",
        "tel",
        "mobile",
        "Phone",
        required=True,
        accepts=r"\d{10}",
        accept_error="Enter a 10 digit mobile number",
    )
    result = run_fill(FakeTransport(fields=[mob]), [_fact("mobile", MOBILE)])
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert mob.value == "9940126718"
    assert result.questions == []  # solved without asking


def test_driver_uses_a_model_repair_guided_by_the_site_error():
    # The site wants the address without commas; no deterministic reshape does that.
    addr = FakeField(
        "a",
        "text",
        "address",
        "Address",
        required=True,
        accepts=r"[^,]+",
        accept_error="Commas are not allowed",
    )
    seen = {}

    def repair(field, rejected, error):
        seen["error"] = error
        return rejected.replace(",", "")

    result = run_fill(
        FakeTransport(fields=[addr]), [_fact("address", "No. 42, Adyar, Chennai")], repair=repair
    )
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert addr.value == "No. 42 Adyar Chennai"
    assert seen["error"] == "Commas are not allowed"


def test_a_repair_that_invents_content_is_refused_and_the_user_is_asked():
    addr = FakeField("a", "text", "address", "Address", required=True, accepts=r"[^,]+")
    result = run_fill(
        FakeTransport(fields=[addr]),
        [_fact("address", "No. 42, Adyar")],
        repair=lambda f, v, e: "221B Baker Street",
    )
    assert addr.value != "221B Baker Street"
    assert result.outcome is RunOutcome.NEEDS_USER
    assert any(q.field_id == "a" for q in result.questions)


def test_optional_fields_are_left_blank_not_asked_about():
    t = FakeTransport(
        fields=[
            FakeField("n", "text", "full_name", "Full name", required=True),
            FakeField("w", "tel", "fld_w", "Work Phone"),  # optional, no fact
        ]
    )
    result = run_fill(t, [_fact("full_name", "Ananya Iyer")])
    assert result.outcome is RunOutcome.COMPLETED, result.detail
    assert result.questions == []
    assert result.left_blank == []  # nothing was even proposed for it


def test_chat_edits_adapt_too():
    mob = FakeField("m", "tel", "mobile", "Mobile Number", value="0000000000", max_length=10)
    edit = apply_edits(FakeTransport(fields=[mob]), [_fact("mobile", MOBILE)], {"mobile"})
    assert edit.filled_fields == ["m"]
    assert mob.value == "9940126718"


def test_a_picker_already_showing_the_target_is_left_alone():
    from agent_backend.driver import _combobox_shows

    picker, _ = _field(input_type="combobox", label="India (भारत): +91")
    assert _combobox_shows(picker, "India")
    assert _combobox_shows(picker, "India (भारत)+91")  # punctuation differs
    assert not _combobox_shows(picker, "Nepal")
