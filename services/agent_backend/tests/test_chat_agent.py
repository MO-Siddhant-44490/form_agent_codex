"""Reasoning chat interpreter: plan parsing, credential safety, fallback."""

from agent_backend.chat_agent import (
    fallback_interpret,
    interpret_or_fallback,
    parse_plan,
)
from agent_backend.model_gateway.fake import FakeModelAdapter


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
