"""Field purpose: masked identifiers are fillable data, credentials/captchas
are the human's, consents need an explicit yes, and an uncertain-but-plausible
mapping is put to the user as a question instead of leaving the field empty.
Modelled on a government e-KYC registration step (Aadhaar as a password-type
input, a consent checkbox, a text captcha)."""

from agent_backend.driver import apply_edits, run_fill
from agent_backend.mapper import DeterministicMapper, MappingSource, ModelAssistedMapper
from agent_backend.memory import InMemoryMappingMemory
from agent_backend.model_gateway.base import GatewayResult
from agent_backend.model_gateway.fake import FakeModelAdapter
from agent_backend.policy import check_action
from agent_backend.purpose import classify_purpose, effective_purpose
from agent_backend.snapshot import build_snapshot, summarize
from agent_backend.transports.fake import FakeField, FakeTransport
from form_contracts import (
    ActionKind,
    BrowserAction,
    DocumentFact,
    ExpectedEffect,
    FactStatus,
    FactValueType,
    FieldMapping,
    FieldMappingBatch,
    FieldPurpose,
    FormField,
    ModelCallMetadata,
    PolicyDecisionKind,
    PolicyRule,
    QuestionKind,
    RunOutcome,
    Sensitivity,
    TargetDescriptor,
)


def _fact(key: str, value: str, sensitivity=Sensitivity.PERSONAL) -> DocumentFact:
    return DocumentFact(
        fact_id=f"f-{key}",
        key=key,
        value=value,
        value_type=FactValueType.STRING,
        confidence=1.0,
        sensitivity=sensitivity,
        status=FactStatus.USER_PROVIDED,
    )


def stub_gateway(mappings: list[FieldMapping]):
    class StubGateway:
        def map_fields(self, request):
            return GatewayResult(
                batch=FieldMappingBatch(mappings=mappings),
                metadata=ModelCallMetadata(
                    model_id="stub",
                    latency_ms=1,
                    schema_valid=True,
                    request_fingerprint=request.fingerprint(),
                ),
            )

    return StubGateway()


def ekyc_fields() -> list[FakeField]:
    return [
        FakeField(
            "aadhaar", "password", "aadhaar_no", "Aadhaar Number/Virtual ID *", required=True
        ),
        FakeField(
            "consent",
            "checkbox",
            "consent",
            "I consent to the use of my Aadhaar details for this scheme.",
            required=True,
            checked=False,
        ),
        FakeField("captcha", "text", "captcha", "Captcha *", required=True),
    ]


# -- classification (same cases as the extension's purpose.test.ts) ----------


def test_classify_purpose_mirrors_perception():
    assert classify_purpose("password", ["Aadhaar Number/Virtual ID *"]) is FieldPurpose.STANDARD
    assert classify_purpose("password", ["PAN Number"]) is FieldPurpose.STANDARD
    assert classify_purpose("password", ["Enter Password"]) is FieldPurpose.CREDENTIAL
    assert classify_purpose("password", []) is FieldPurpose.CREDENTIAL
    assert classify_purpose("password", ["Aadhaar OTP"]) is FieldPurpose.CREDENTIAL
    assert classify_purpose("text", ["Enter OTP"]) is FieldPurpose.CREDENTIAL
    assert classify_purpose("text", ["Code"], "one-time-code") is FieldPurpose.CREDENTIAL
    assert classify_purpose("text", ["PIN Code"]) is FieldPurpose.STANDARD
    assert classify_purpose("text", ["Pincode", "pincode"]) is FieldPurpose.STANDARD
    assert classify_purpose("text", ["Captcha *"]) is FieldPurpose.CAPTCHA
    assert (
        classify_purpose("checkbox", ["I agree to the Terms & Conditions"]) is FieldPurpose.CONSENT
    )
    assert classify_purpose("checkbox", ["Subscribe to newsletter"]) is FieldPurpose.STANDARD


def test_effective_purpose_can_only_tighten():
    # An observation claiming a "Password"-labelled password box is standard
    # does not get to relax the rule (defence in depth at the gate).
    pw = FormField(
        field_id="pw",
        target=TargetDescriptor(field_id="pw", role="textbox", name_attr="password"),
        input_type="password",
        label="Password",
        value_redacted=True,
        purpose=FieldPurpose.STANDARD,
    )
    assert effective_purpose(pw) is FieldPurpose.CREDENTIAL
    # ...while perception's stricter verdict is kept.
    box = FormField(
        field_id="x",
        target=TargetDescriptor(field_id="x", role="checkbox"),
        input_type="checkbox",
        label="Tick to proceed",
        purpose=FieldPurpose.CONSENT,
    )
    assert effective_purpose(box) is FieldPurpose.CONSENT


# -- the fake transport perceives like the extension -------------------------


def test_fake_transport_classifies_and_redacts_like_perception():
    obs = FakeTransport(fields=ekyc_fields()).observe()
    by_id = {f.field_id: f for f in obs.fields}
    assert by_id["aadhaar"].purpose is FieldPurpose.STANDARD
    assert by_id["aadhaar"].value_redacted is True
    assert by_id["consent"].purpose is FieldPurpose.CONSENT
    assert by_id["captcha"].purpose is FieldPurpose.CAPTCHA


# -- policy gate ------------------------------------------------------------


def _action(field_id: str, value: str) -> BrowserAction:
    return BrowserAction.model_validate(
        dict(
            action_id=f"a-{field_id}",
            run_id="run-fake",
            tab_id=1,
            origin="http://fake.test",
            sequence_number=1,
            kind=ActionKind.SET_TEXT,
            target=TargetDescriptor(field_id=field_id, role="textbox"),
            resolved_value=value,
            expected_effect=ExpectedEffect(field_value=value, validation_error=False),
            idempotency_key=f"k-{field_id}",
        )
    )


def test_gate_allows_masked_identifier_blocks_captcha_and_credential():
    obs = FakeTransport(
        fields=[*ekyc_fields(), FakeField("pw", "password", "password", "Password")]
    ).observe()
    ok = check_action(_action("aadhaar", "976487322387"), obs, {"aadhaar": "976487322387"})
    assert ok.decision is PolicyDecisionKind.ALLOW
    cap = check_action(_action("captcha", "x7k2"), obs, {"captcha": "x7k2"})
    assert cap.rule is PolicyRule.HUMAN_ONLY_FIELD
    pw = check_action(_action("pw", "hunter2"), obs, {"pw": "hunter2"})
    assert pw.rule is PolicyRule.CREDENTIAL_FIELD


# -- mapping ----------------------------------------------------------------


def test_masked_identifier_maps_and_fills_captcha_is_left_to_the_human():
    transport = FakeTransport(fields=ekyc_fields())
    facts = [_fact("aadhaar_no", "976487322387"), _fact("captcha", "never")]
    result = run_fill(transport, facts)
    assert "aadhaar" in result.filled_fields
    assert "captcha" not in result.filled_fields  # even with a same-named fact
    assert transport.fields[0].value == "976487322387"
    assert transport.fields[2].value is None
    # Not abandoned as a "captcha page": the outcome asks for what's left.
    assert result.outcome is RunOutcome.NEEDS_USER
    assert "login" not in (result.detail or "")


def test_captcha_widget_on_the_page_does_not_abandon_the_fill():
    transport = FakeTransport(fields=ekyc_fields())
    transport.captcha_page = True
    result = run_fill(transport, [_fact("aadhaar_no", "976487322387")])
    assert "aadhaar" in result.filled_fields


def test_consent_is_never_inferred_only_set_on_explicit_say_so():
    fields = ekyc_fields()
    gateway = stub_gateway([FieldMapping(field_id="consent", fact_key="agree", confidence=0.99)])
    memory = InMemoryMappingMemory()
    transport = FakeTransport(fields=fields)
    obs = transport.observe()
    # A "yes"-valued fact with a different key: the model would happily bind it.
    outcome = ModelAssistedMapper(gateway, memory=memory).map(obs, {"agree": _fact("agree", "yes")})
    assert "consent" not in outcome.assignments
    # A fact keyed to that very field (the user's answer) does bind, deterministically.
    outcome = DeterministicMapper().map(obs, {"consent": _fact("consent", "yes")})
    assert outcome.assignments["consent"].checked is True


def test_uncertain_candidate_becomes_a_use_it_question_not_silence():
    """The model sees "Aadhaar Number" and a fact "aID": plausible but not
    certain -> the user is asked "use it?", replacing the bare "no fact" question."""
    gateway = stub_gateway(
        [
            FieldMapping(
                field_id="aadhaar", fact_key="aID", confidence=0.55, needs_clarification=True
            )
        ]
    )
    obs = FakeTransport(fields=ekyc_fields()).observe()
    outcome = ModelAssistedMapper(gateway).map(obs, {"aID": _fact("aID", "976487322387")})
    assert "aadhaar" not in outcome.assignments
    qs = [q for q in outcome.questions if q.field_id == "aadhaar"]
    assert len(qs) == 1
    assert qs[0].kind is QuestionKind.LOW_CONFIDENCE
    assert qs[0].fact_keys == ["aID"]
    assert "aID" in qs[0].prompt and "Use it" in qs[0].prompt


# -- the user's answer: bind / one-off value ---------------------------------


def test_user_binding_fills_the_field_and_is_remembered():
    transport = FakeTransport(fields=ekyc_fields())
    memory = InMemoryMappingMemory()
    edit = apply_edits(
        transport,
        [_fact("aID", "976487322387")],
        {"aID"},
        bindings={"aadhaar": "aID"},
        memory=memory,
    )
    assert edit.filled_fields == ["aadhaar"]
    assert transport.fields[0].value == "976487322387"
    # The binding is remembered for this site, so a revisit maps without asking.
    obs = transport.observe()
    recalled = ModelAssistedMapper(FakeModelAdapter(), memory=memory).map(
        obs, {"aID": _fact("aID", "976487322387")}
    )
    assert recalled.assignments["aadhaar"].fact.key == "aID"
    assert recalled.assignments["aadhaar"].source is MappingSource.MEMORY


def test_one_off_field_value_is_not_remembered():
    transport = FakeTransport(fields=ekyc_fields())
    memory = InMemoryMappingMemory()
    edit = apply_edits(
        transport,
        [_fact("field:consent", "yes")],
        {"field:consent"},
        bindings={"consent": "field:consent"},
        memory=memory,
    )
    assert edit.filled_fields == ["consent"]
    assert transport.fields[1].checked is True
    assert memory._store == {}


# -- snapshot ---------------------------------------------------------------


def test_snapshot_separates_yours_to_complete_from_needs_a_value():
    transport = FakeTransport(
        fields=[*ekyc_fields(), FakeField("city", "text", "city", "City", required=True)]
    )
    snap = build_snapshot(transport.observe())
    by_id = {s["field_id"]: s for s in snap}
    assert by_id["captcha"]["human_only"] is True
    assert by_id["consent"]["consent"] is True
    assert by_id["aadhaar"]["human_only"] is False
    text = summarize(snap)["text"]
    assert "Still needs a value: Aadhaar Number/Virtual ID *, City." in text
    assert "Needs your decision: I consent" in text
    assert "Yours to complete on the page: Captcha *." in text


def test_masked_field_counts_as_filled_by_length_only():
    f = ekyc_fields()[0]
    f.value = "976487322387"
    snap = build_snapshot(FakeTransport(fields=[f]).observe())
    assert snap[0]["filled"] is True
    assert snap[0]["value"] is None  # the value never leaves the page
