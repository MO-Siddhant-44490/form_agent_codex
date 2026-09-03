"""Gateway factory: Bedrock is the default provider; env overrides select
fake/local/none. No live credentials are needed — we only assert the wiring."""

from agent_backend.mapper import DeterministicMapper, ModelAssistedMapper
from agent_backend.model_gateway.bedrock import BedrockModelAdapter
from agent_backend.model_gateway.factory import (
    DEFAULT_BEDROCK_MODEL_ID,
    build_default_mapper,
    build_gateway_from_env,
)
from agent_backend.model_gateway.fake import FakeModelAdapter


def test_bedrock_is_the_default_provider(monkeypatch):
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    gateway = build_gateway_from_env()
    assert isinstance(gateway, BedrockModelAdapter)
    assert gateway.model_id == DEFAULT_BEDROCK_MODEL_ID


def test_model_id_and_region_come_from_env(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "apac.anthropic.claude-custom:0")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_PROFILE", "dev")
    gateway = build_gateway_from_env()
    assert gateway.model_id == "apac.anthropic.claude-custom:0"
    assert gateway._config.region == "ap-south-1"
    assert gateway._config.profile == "dev"


def test_fake_provider_selectable(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    assert isinstance(build_gateway_from_env(), FakeModelAdapter)


def test_default_mapper_is_model_assisted(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    assert isinstance(build_default_mapper(), ModelAssistedMapper)


def test_provider_none_forces_deterministic(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    assert isinstance(build_default_mapper(), DeterministicMapper)


def test_bedrock_outage_degrades_to_abstention_not_crash(monkeypatch):
    """The application default must survive a missing/expired credential: the
    mapper asks the user, it does not raise (plan.md §11.2)."""
    from agent_backend.model_gateway.base import ModelUnavailable
    from agent_backend.transports.fake import FakeTransport, basic_form_fields

    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    mapper = build_default_mapper()

    class _Down:
        def map_fields(self, request):
            raise ModelUnavailable("expired token")

    mapper._gateway = _Down()  # simulate Bedrock unreachable
    fields = basic_form_fields()
    for i, f in enumerate(fields):
        f.name = f"fld_{i}"  # force the model path (no deterministic matches)
    from agent_backend.facts import slice1_facts

    outcome = mapper.map(FakeTransport(fields=fields).observe(), {f.key: f for f in slice1_facts()})
    assert outcome.assignments == {}  # abstained, did not crash
    assert outcome.questions  # required fields surfaced for the user
