"""Reasoning chat interpreter: plan parsing, credential safety, fallback."""

from agent_backend.chat_agent import (
    clamp_length,
    fallback_interpret,
    interpret_or_fallback,
    parse_char_limit,
    parse_plan,
    repair_values,
)
from agent_backend.model_gateway.fake import FakeModelAdapter


def test_parse_char_limit():
    assert parse_char_limit("shorten the address to under 50 characters") == 50
    assert parse_char_limit("keep it to 30 chars") == 30
    assert parse_char_limit("make it shorter please") is None


def test_clamp_length_backs_off_to_a_word_boundary():
    v = "Flat 1204 Sunbeam Hts, Plot 27, Palm Beach Road, Navi Mumbai"
    out = clamp_length(v, 40)
    assert len(out) <= 40
    assert not out.endswith(",")
    assert out == "Flat 1204 Sunbeam Hts, Plot 27, Palm"  # cut at the last word boundary
    assert clamp_length("short", 40) == "short"  # already fits


def test_repair_values_reads_corrections_and_drops_credentials():
    gw = FakeModelAdapter()
    gw.chat_response = '{"corrections": {"address": "Flat 1204, Palm Beach Rd", "password": "x"}}'
    out = repair_values(
        gw, [{"key": "address", "label": "Address", "value": "long...", "error": "too long"}]
    )
    assert out == {"address": "Flat 1204, Palm Beach Rd"}


def test_repair_values_empty_without_gateway_or_issues():
    assert repair_values(None, [{"key": "a"}]) == {}
    assert repair_values(FakeModelAdapter(), []) == {}


def test_parse_plan_reads_reply_and_ops():
    plan = parse_plan(
        '{"reply": "Shortened the address.", '
        '"ops": [{"op": "set_fact", "key": "address", "value": "Flat 1204, Palm Beach Rd"}]}'
    )
    assert plan.reply == "Shortened the address."
    assert plan.fact_updates() == [{"key": "address", "value": "Flat 1204, Palm Beach Rd"}]
    assert plan.wants_refill() is True


def test_parse_plan_extracts_json_from_prose():
    plan = parse_plan('Sure! ```json\n{"reply":"ok","ops":[{"op":"refill"}]}\n``` done')
    assert plan.wants_refill() is True
    assert plan.fact_updates() == []


def test_parse_plan_drops_credential_set_ops():
    plan = parse_plan(
        '{"reply":"x","ops":['
        '{"op":"set_fact","key":"password","value":"hunter2"},'
        '{"op":"set_fact","key":"otp","value":"123456"},'
        '{"op":"set_fact","key":"cc_number","value":"4111..."},'
        '{"op":"set_fact","key":"pincode","value":"560001"}]}'
    )
    # Only the non-credential pincode survives.
    assert plan.fact_updates() == [{"key": "pincode", "value": "560001"}]


def test_explain_op_does_not_trigger_refill():
    plan = parse_plan('{"reply":"here","ops":[{"op":"explain","text":"3 fields left"}]}')
    assert plan.wants_refill() is False
    assert plan.fact_updates() == []


def test_fallback_handles_set_and_refill_and_unknown():
    assert fallback_interpret("refill").ops[0].op == "refill"
    set_plan = fallback_interpret("set state to Karnataka")
    assert set_plan.fact_updates() == [{"key": "state", "value": "Karnataka"}]
    assert fallback_interpret("what did you fill?").ops == []


def test_interpret_or_fallback_uses_the_gateway_plan():
    gw = FakeModelAdapter()
    gw.chat_response = '{"reply":"done","ops":[{"op":"set_fact","key":"city","value":"Pune"}]}'
    plan = interpret_or_fallback(gw, "set city to Pune", [], [])
    assert plan.reply == "done"
    assert plan.fact_updates() == [{"key": "city", "value": "Pune"}]


def test_interpret_or_fallback_recovers_from_bad_model_output():
    gw = FakeModelAdapter()
    gw.chat_response = "not json at all"
    # Falls back to deterministic parsing of the user's text.
    plan = interpret_or_fallback(gw, "refill", [], [])
    assert plan.ops[0].op == "refill"


def test_bind_op_binds_a_fact_to_a_field_instead_of_storing_a_value():
    # "aid is my aadhaar id" is a statement about MEANING, not a value.
    plan = parse_plan(
        '{"reply":"Using aID for Aadhaar Number.","ops":['
        '{"op":"bind","field":"Aadhaar Number/Virtual ID *","key":"aID"},'
        '{"op":"bind","field":"Password","key":"password"}]}'
    )
    assert plan.fact_updates() == []  # nothing written into the profile
    assert plan.bindings() == [{"field": "Aadhaar Number/Virtual ID *", "key": "aID"}]


def test_fallback_understands_use_x_for_y():
    plan = fallback_interpret("use aID for Aadhaar Number")
    assert plan.bindings() == [{"field": "Aadhaar Number", "key": "aID"}]
    assert plan.fact_updates() == []
