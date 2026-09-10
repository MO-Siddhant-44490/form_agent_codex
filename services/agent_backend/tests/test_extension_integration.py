"""End-to-end Slice 1 acceptance: the Python driver fills the real fixture
form through the real extension, one verified action at a time, and stops
before submission.

Requires a built extension (pnpm --filter @form-agent/extension build), the
fixture server dependencies (none), and a Playwright chromium. Gated behind
RUN_EXTENSION_INTEGRATION=1 to keep default test runs browser-free.
"""

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
from agent_backend.driver import run_fill
from agent_backend.facts import slice1_facts
from agent_backend.transports.extension_playwright import ExtensionPlaywrightTransport
from form_contracts import RunOutcome, VerificationStatus

REPO = Path(__file__).resolve().parents[3]
EXTENSION_DIST = REPO / "apps" / "extension" / "dist"
FIXTURE_PORT = 4174
FIXTURE_URL = f"http://127.0.0.1:{FIXTURE_PORT}/basic-form/"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EXTENSION_INTEGRATION") != "1",
    reason="set RUN_EXTENSION_INTEGRATION=1 to run browser integration",
)


@pytest.fixture(scope="module")
def fixture_server():
    process = subprocess.Popen(
        ["node", str(REPO / "apps" / "fixtures" / "serve.mjs")],
        env={**os.environ, "FIXTURE_PORT": str(FIXTURE_PORT)},
    )
    for _ in range(150):
        try:
            with socket.create_connection(("127.0.0.1", FIXTURE_PORT), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        process.terminate()
        pytest.fail("fixture server did not start")
    yield
    process.terminate()
    process.wait(timeout=5)


def test_python_driver_fills_real_form_via_extension(fixture_server):
    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(EXTENSION_DIST, FIXTURE_URL)
    try:
        result = run_fill(transport, slice1_facts())

        assert result.outcome is RunOutcome.COMPLETED, result.detail
        assert len(result.filled_fields) == 8
        assert all(v.status is VerificationStatus.SUCCESS for v in result.verifications)

        # Ground truth straight from the live DOM, independent of observations.
        dom = transport.page_eval(
            """() => {
                const form = document.getElementById("application-form");
                const data = Object.fromEntries(new FormData(form).entries());
                data.subscribe = form.elements.subscribe.checked;
                data.honeypot = form.elements.website.value;
                data.submissions = window.__fixture.submissions.length;
                return data;
            }"""
        )
        assert dom["full_name"] == "Ada Lovelace"
        assert dom["email"] == "ada@example.test"
        assert dom["date_of_birth"] == "1998-04-17"
        assert dom["country"] == "IN"
        assert dom["contact_method"] == "email"
        assert dom["subscribe"] is True
        assert dom["honeypot"] == ""  # bot trap untouched
        assert dom["submissions"] == 0  # invariant 1: stopped before submit
    finally:
        transport.close()


def test_model_assisted_fill_on_tricky_form_via_extension(fixture_server):
    """Slice 3 in a real browser: obscure name attributes force the
    model-assisted path (fake adapter); one label carries a prompt-injection
    attempt that must change nothing."""
    from agent_backend.mapper import ModelAssistedMapper
    from agent_backend.model_gateway.fake import FakeModelAdapter
    from form_contracts import Sensitivity

    facts = [
        f.model_copy(update={"value": "India", "sensitivity": Sensitivity.PUBLIC})
        if f.key == "country"
        else f
        for f in slice1_facts()
        if f.key in {"full_name", "email", "date_of_birth", "country", "contact_method"}
    ]

    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(
        EXTENSION_DIST, f"http://127.0.0.1:{FIXTURE_PORT}/tricky-form/"
    )
    try:
        result = run_fill(transport, facts, mapper=ModelAssistedMapper(FakeModelAdapter()))
        assert result.outcome is RunOutcome.COMPLETED, result.detail
        assert len(result.filled_fields) == 5
        assert len(result.model_calls) == 1

        dom = transport.page_eval(
            """() => {
                const form = document.getElementById("application-form");
                return Object.fromEntries(new FormData(form).entries());
            }"""
        )
        assert dom == {
            "fld_a1": "Ada Lovelace",
            "fld_a2": "ada@example.test",
            "fld_a3": "1998-04-17",
            "fld_a4": "IN",
            "fld_a5": "email",
        }
    finally:
        transport.close()


def test_langgraph_fill_and_submit_via_extension(fixture_server):
    """Slice 4 in a real browser: the durable graph fills the fixture form,
    pauses for submission approval, and submits only after an explicit token
    (fixture-local). Extension enforces the submission lock throughout."""
    from datetime import UTC, datetime, timedelta

    from agent_backend.orchestration.graph import build_form_fill_graph
    from agent_backend.orchestration.runner import resume_run, start_run
    from form_contracts import ApprovalToken, RunOutcome, TargetDescriptor

    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(EXTENSION_DIST, FIXTURE_URL)
    # Fixture mode lets the controlled form accept a token-backed submit.
    try:
        session = transport.attach()
        transport._worker.evaluate("() => globalThis.__formAgentTest.setFixtureMode(true)")

        graph = build_form_fill_graph(_PreAttached(transport, session))
        handle = start_run(
            graph,
            session.run_id,
            slice1_facts(),
            goal="fill_and_submit",
            submit_target=TargetDescriptor(
                field_id="submit-btn", role="button", input_type="submit"
            ),
        )
        assert handle.interrupt["reason"] == "submission_approval"
        submissions = transport.page_eval("() => window.__fixture.submissions.length")
        assert submissions == 0  # paused before submit

        token = ApprovalToken(
            token_id="tok-graph",
            run_id=session.run_id,
            origin=session.origin,
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        transport._worker.evaluate(
            "(t) => globalThis.__formAgentTest.grantApproval(t)", token.model_dump(mode="json")
        )
        resumed = resume_run(
            graph,
            session.run_id,
            {"approved": True, "token": token.model_dump(mode="json")},
        )
        assert resumed.state["outcome"] == RunOutcome.COMPLETED.value
        assert resumed.state["submitted"] is True
        assert transport.page_eval("() => window.__fixture.submissions.length") == 1
    finally:
        transport.close()


class _PreAttached:
    """Transport wrapper that reuses an already-attached extension session so
    the graph's attach node does not re-launch the browser."""

    def __init__(self, inner, session):
        self._inner = inner
        self._session = session

    def attach(self):
        return self._session

    def observe(self):
        return self._inner.observe()

    def execute(self, action):
        return self._inner.execute(action)

    def close(self):
        pass


def test_multipage_fill_via_extension(fixture_server):
    """Slice 5 multi-page in a real browser: the driver fills each page,
    clicks Continue, re-perceives the revealed page, and stops before submit."""
    from agent_backend.driver import run_fill

    facts = [
        f
        for f in slice1_facts()
        if f.key in {"full_name", "email", "date_of_birth", "country", "years_experience"}
    ]
    # country fact needs to match the option value "IN"
    facts = [f.model_copy(update={"value": "IN"}) if f.key == "country" else f for f in facts]

    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(
        EXTENSION_DIST, f"http://127.0.0.1:{FIXTURE_PORT}/multipage-form/"
    )
    try:
        result = run_fill(transport, facts)
        assert result.outcome is RunOutcome.COMPLETED, result.detail
        assert len(result.filled_fields) == 5  # across 3 pages

        # The last page is showing and nothing was submitted (invariant 1).
        state = transport.page_eval(
            """() => ({
                page: window.__fixture.currentPage,
                submissions: window.__fixture.submissions.length,
                values: Object.fromEntries(
                    new FormData(document.getElementById("application-form")).entries()),
            })"""
        )
        assert state["page"] == 2  # navigated to the final page
        assert state["submissions"] == 0
        assert state["values"] == {
            "full_name": "Ada Lovelace",
            "email": "ada@example.test",
            "date_of_birth": "1998-04-17",
            "country": "IN",
            "years_experience": "5",
        }
    finally:
        transport.close()


def test_combobox_fill_via_extension(fixture_server):
    """Slice 5 custom widget: a searchable ARIA combobox filled in a real
    browser — the executor opens the popup and clicks the matching option."""
    from agent_backend.driver import run_fill
    from form_contracts import Sensitivity

    facts = [
        f.model_copy(update={"value": "IN", "sensitivity": Sensitivity.PUBLIC})
        if f.key == "country"
        else f
        for f in slice1_facts()
        if f.key in {"full_name", "country"}
    ]
    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(
        EXTENSION_DIST, f"http://127.0.0.1:{FIXTURE_PORT}/combobox-form/"
    )
    try:
        result = run_fill(transport, facts)
        assert result.outcome is RunOutcome.COMPLETED, result.detail
        assert len(result.filled_fields) == 2

        values = transport.page_eval(
            "() => Object.fromEntries("
            "new FormData(document.getElementById('application-form')).entries())"
        )
        assert values["full_name"] == "Ada Lovelace"
        assert values["country"] == "IN"  # the hidden input the combobox sets
    finally:
        transport.close()


def test_dialog_dismiss_then_fill_via_extension(fixture_server):
    """Slice 5 dialog dismissal in a real browser: the cookie banner is
    dismissed (preferring Reject — no permission granted), then the form is
    filled underneath."""
    from agent_backend.driver import run_fill

    facts = [f for f in slice1_facts() if f.key in {"full_name", "email"}]
    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(
        EXTENSION_DIST, f"http://127.0.0.1:{FIXTURE_PORT}/dialog-form/"
    )
    try:
        result = run_fill(transport, facts)
        assert result.outcome is RunOutcome.COMPLETED, result.detail
        assert len(result.filled_fields) == 2

        state = transport.page_eval(
            """() => ({
                bannerGone: !document.getElementById("cookie-banner"),
                permissionGranted: window.__fixture.permissionGranted,
                values: Object.fromEntries(
                    new FormData(document.getElementById("application-form")).entries()),
            })"""
        )
        assert state["bannerGone"] is True  # dialog dismissed
        assert state["permissionGranted"] is False  # Reject chosen, not Accept
        assert state["values"] == {"full_name": "Ada Lovelace", "email": "ada@example.test"}
    finally:
        transport.close()


def test_file_upload_via_extension(fixture_server):
    """Slice 5 file upload in a real browser: the executor attaches a document
    to a file input via DataTransfer."""
    import base64

    from agent_backend.driver import run_fill
    from form_contracts import UploadFileRef

    facts = [f for f in slice1_facts() if f.key == "full_name"]
    upload = UploadFileRef(
        filename="resume.pdf",
        mime_type="application/pdf",
        content_base64=base64.b64encode(b"%PDF-1.7 real resume bytes").decode(),
    )
    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(
        EXTENSION_DIST, f"http://127.0.0.1:{FIXTURE_PORT}/upload-form/"
    )
    try:
        result = run_fill(transport, facts, uploads={"resume": upload})
        assert result.outcome is RunOutcome.COMPLETED, result.detail
        assert "resume" in result.filled_fields

        uploaded = transport.page_eval("() => window.__fixture.uploaded")
        assert uploaded is not None
        assert uploaded["name"] == "resume.pdf"
        assert uploaded["type"] == "application/pdf"
        assert uploaded["size"] == len(b"%PDF-1.7 real resume bytes")  # real bytes attached
    finally:
        transport.close()


def test_datepicker_fill_via_extension(fixture_server):
    """Slice 5 custom date picker in a real browser: the executor opens the
    calendar and clicks the day cell matching the date-of-birth fact."""
    from agent_backend.driver import run_fill

    facts = [f for f in slice1_facts() if f.key in {"full_name", "date_of_birth"}]
    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(
        EXTENSION_DIST, f"http://127.0.0.1:{FIXTURE_PORT}/datepicker-form/"
    )
    try:
        result = run_fill(transport, facts)
        assert result.outcome is RunOutcome.COMPLETED, result.detail
        assert len(result.filled_fields) == 2

        values = transport.page_eval(
            "() => Object.fromEntries("
            "new FormData(document.getElementById('application-form')).entries())"
        )
        assert values["full_name"] == "Ada Lovelace"
        assert values["date_of_birth"] == "1998-04-17"  # picked from the calendar
    finally:
        transport.close()


def test_shadow_mode_proposes_without_touching_the_page(fixture_server):
    """Slice 6 shadow mode in a real browser: propose a full plan from a live
    page and confirm the DOM was never modified (safe on live sites)."""
    from agent_backend.evaluation.metrics import GroundTruth, score_plan
    from agent_backend.evaluation.shadow import propose_plan

    transport = ExtensionPlaywrightTransport(EXTENSION_DIST, FIXTURE_URL)
    try:
        # propose_plan attaches + observes internally; it must not execute.
        plan = propose_plan(transport, slice1_facts())

        assert len(plan.proposed_actions) >= 5
        assert plan.unsafe_proposals == []
        assert all(p.action.kind.value != "SUBMIT" for p in plan.proposed_actions)

        # The served form started empty and proposing changed nothing.
        values_after = transport.page_eval(
            "() => Object.fromEntries("
            "new FormData(document.getElementById('application-form')).entries())"
        )
        assert all(v == "" for v in values_after.values())  # untouched
        assert transport.page_eval("() => window.__fixture.submissions.length") == 0

        card = score_plan(
            plan,
            GroundTruth(
                fixture_id="basic-form",
                expected_fill={"full-name": "Ada Lovelace", "email": "ada@example.test"},
            ),
        )
        assert card.mapping_accuracy == 1.0
    finally:
        transport.close()


def test_role_based_widgets_fill_in_document_order_via_extension(fixture_server):
    """A Google-Forms-shaped page: div radios, a div listbox, aria-required,
    aria-labelledby headings. Perceived by ROLE, executed by role, verified from
    the fresh observation — in document order, no site-specific code."""
    from agent_backend.mapper import ModelAssistedMapper
    from agent_backend.model_gateway.fake import FakeModelAdapter
    from form_contracts import DocumentFact, FactStatus, FactValueType, Sensitivity

    def fact(key, value, vt=FactValueType.STRING):
        return DocumentFact(
            fact_id=f"f-{key}",
            key=key,
            value=value,
            value_type=vt,
            confidence=1.0,
            sensitivity=Sensitivity.PUBLIC,
            status=FactStatus.USER_PROVIDED,
        )

    facts = [
        fact("full_name", "Ananya Prakash Iyer"),
        fact("date_of_birth", "1988-09-23", FactValueType.DATE),
        fact("gender", "Female"),
        fact("marital_status", "Divorced"),
        fact("address", "No. 42, Second Cross Street, Adyar, Chennai 600020"),
        fact("country", "India"),
    ]
    assert EXTENSION_DIST.exists(), "build the extension first"
    transport = ExtensionPlaywrightTransport(
        EXTENSION_DIST, f"http://127.0.0.1:{FIXTURE_PORT}/aria-form/"
    )
    try:
        result = run_fill(transport, facts, mapper=ModelAssistedMapper(FakeModelAdapter()))
        assert result.outcome is RunOutcome.COMPLETED, (result.detail, result.questions)
        # Filled top to bottom: the ARIA widgets sit between native inputs.
        labels = [
            f.label or f.accessible_name
            for f in transport.observe().fields
            if f.field_id in result.filled_fields
        ]
        assert labels == [
            "Full name *",
            "Date of birth",
            "Gender",
            "Marital status",
            "Address",
            "Country",
        ]
        dom = transport.page_eval(
            """() => ({
                name: document.querySelector('input[type=text]').value,
                dob: document.querySelector('input[type=date]').value,
                gender: document.querySelector('[role=radio][aria-checked=true]')?.dataset.value,
                marital: document.querySelector('[role=option][aria-selected=true]')?.dataset.value,
                address: document.querySelector('textarea').value,
            })"""
        )
        assert dom == {
            "name": "Ananya Prakash Iyer",
            "dob": "1988-09-23",
            "gender": "Female",
            "marital": "Divorced",
            "address": "No. 42, Second Cross Street, Adyar, Chennai 600020",
        }
    finally:
        transport.close()
