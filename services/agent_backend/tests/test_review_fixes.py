"""Regression tests for the system review (2026-09-30): safety and
correctness defects found by reading the code, each pinned here."""

from types import SimpleNamespace

from agent_backend.adapt import plausible_reshape
from agent_backend.driver import run_fill
from agent_backend.mapper import ModelAssistedMapper
from agent_backend.model_gateway.fake import FakeModelAdapter
from agent_backend.policy import check_action
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import (
    ActionKind,
    BrowserAction,
    Derivation,
    DocumentFact,
    ExpectedEffect,
    FactStatus,
    FactValueType,
    PolicyDecisionKind,
    QuestionKind,
    Sensitivity,
    TargetDescriptor,
)


def _fact(key, value, sensitivity=Sensitivity.PERSONAL, fid=None):
    return DocumentFact(
        fact_id=fid or f"f-{key}",
        key=key,
        value=value,
        value_type=FactValueType.STRING,
        confidence=1.0,
        sensitivity=sensitivity,
        status=FactStatus.USER_PROVIDED,
    )


class Deriver:
    """A derivation engine that 'computes' whatever it is asked for."""

    def __init__(self, value, source_ids, confidence=0.95):
        self.value, self.source_ids, self.confidence = value, source_ids, confidence

    def derive(self, available, targets, today=None):
        facts = [
            DocumentFact(
                fact_id=f"derived-{t.key}",
                key=t.key,
                value=self.value,
                value_type=FactValueType.STRING,
                confidence=self.confidence,
                sensitivity=Sensitivity.PERSONAL,
                status=FactStatus.DERIVED,
                derivation=Derivation(
                    operation="copy", source_fact_ids=self.source_ids, explanation=""
                ),
            )
            for t in targets
        ]
        return SimpleNamespace(facts=facts, model_calls=[])


def test_derivation_never_ticks_a_consent_box():
    box = FakeField(
        "tos",
        "checkbox",
        "fld_tos",
        "I agree to the Terms and Conditions",
        required=True,
        checked=False,
    )
    obs = FakeTransport(fields=[box]).observe()
    mapper = ModelAssistedMapper(FakeModelAdapter(), derivation_engine=Deriver("yes", ["f-name"]))
    outcome = mapper.map(obs, {"name": _fact("name", "Ananya")})
    assert "tos" not in outcome.assignments


def test_value_derived_from_a_sensitive_fact_needs_confirmation():
    aad = _fact("aadhaar", "976487322387", Sensitivity.SENSITIVE, fid="f-aad")
    field = FakeField("re", "text", "fld_re", "Re-enter your ID to verify", required=True)
    obs = FakeTransport(fields=[field]).observe()
    mapper = ModelAssistedMapper(
        FakeModelAdapter(), derivation_engine=Deriver("976487322387", ["f-aad"])
    )
    outcome = mapper.map(obs, {"aadhaar": aad})
    assert "re" not in outcome.assignments
    assert any(q.kind is QuestionKind.SENSITIVE_MAPPING for q in outcome.questions)


def test_low_confidence_derivation_is_not_filled():
    field = FakeField("age", "number", "fld_age", "Age", required=True)
    obs = FakeTransport(fields=[field]).observe()
    mapper = ModelAssistedMapper(
        FakeModelAdapter(), derivation_engine=Deriver("38", ["f-dob"], confidence=0.4)
    )
    outcome = mapper.map(obs, {"dob": _fact("dob", "1988-09-23", fid="f-dob")})
    assert "age" not in outcome.assignments


def test_repair_provenance_rejects_new_content_with_the_same_digits():
    assert not plausible_reshape("ananya88@gmail.com", "attacker88@evil.io")
    assert not plausible_reshape("No. 42, Adyar", "No. 42, Mylapore")  # a different place
    assert plausible_reshape("+91 99401 26718", "9940126718")
    assert plausible_reshape("No. 42, Second Cross Street, Adyar", "No 42, 2nd Cross St, Adyar")


def _action(field_id, value, name_attr=None):
    return BrowserAction.model_validate(
        dict(
            action_id="a",
            run_id="run-fake",
            tab_id=1,
            origin="http://fake.test",
            sequence_number=1,
            kind=ActionKind.SET_TEXT,
            target=TargetDescriptor(field_id=field_id, role="textbox", name_attr=name_attr),
            resolved_value=value,
            expected_effect=ExpectedEffect(field_value=value, validation_error=False),
            idempotency_key="k",
        )
    )


def test_gate_checks_the_exact_field_not_a_same_named_neighbour():
    # An earlier STANDARD field shares the name with a password box further down.
    obs = FakeTransport(
        fields=[
            FakeField("user", "text", "login", "Username"),
            FakeField("pw", "password", "login", "Password"),
        ]
    ).observe()
    decision = check_action(_action("pw", "x", name_attr="login"), obs, {"pw": "x"})
    assert decision.decision is PolicyDecisionKind.BLOCK  # judged as the password it is


def test_sensitive_values_are_never_sent_for_model_repair():
    asked = []
    pan = FakeField("pan", "text", "pan_no", "PAN", required=True, accepts=r"[A-Z]{5}\d{4}[A-Z]")
    run_fill(
        FakeTransport(fields=[pan]),
        [_fact("pan_no", "abcde1234f", Sensitivity.SENSITIVE)],
        repair=lambda f, v, e: asked.append(v) or None,
    )
    assert asked == []


def test_a_second_operation_on_a_busy_session_is_refused_not_interleaved():
    import threading

    from agent_backend.api import operations
    from agent_backend.api.session_hub import ExtensionSession

    sent = []

    class Loop:  # run_coroutine_threadsafe stand-in: run the coroutine now
        pass

    session = ExtensionSession(run_id="r", origin="http://fake.test", tab_id=1, send=None)
    session.op_lock.acquire()  # an operation is already running
    started = threading.Event()

    async def send(payload):
        sent.append(payload)

    import asyncio

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        operations._run_in_worker(
            SimpleNamespace(), session, loop, send, lambda tr: started.set(), event="fill"
        )
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), loop).result(1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
    assert not started.is_set()  # the work never ran
    assert sent and sent[0]["type"] == "fill_error"


def test_errors_shown_to_the_user_never_carry_field_values():
    from agent_backend.api.operations import _safe_error
    from form_contracts import PageObservation
    from pydantic import ValidationError

    try:
        PageObservation.model_validate(
            {"run_id": "x", "fields": [{"field_id": "f", "current_value": "SECRET-976487322387"}]}
        )
    except ValidationError as error:
        text = _safe_error(error)
    assert "976487322387" not in text
