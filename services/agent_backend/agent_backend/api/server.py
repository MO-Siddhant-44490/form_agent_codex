"""Uvicorn entrypoint (server extra). `uv run --extra server python -m
agent_backend.api.server`."""

import os

from ..document_intelligence.fact_store import FactStore
from ..document_intelligence.store import DocumentStore
from ..model_gateway.factory import build_default_mapper, selected_provider
from ..persistence.repository import Repository, RepositoryMappingMemory, make_engine
from .app import AppState, create_app
from .auth import DevTokenAuth
from .config import BackendConfig


def load_dotenv(path: str = ".env") -> None:
    """Read KEY=VALUE lines from ./.env into the environment (never overriding
    a variable that is already set), so a user can paste their API key into a
    file instead of exporting it. Lines starting with # are ignored."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


_FIX_HINT = {
    "openai": "set OPENAI_API_KEY in .env (or export it), then restart",
    "bedrock": "export AWS_PROFILE and AWS_REGION (and log in, e.g. `aws sso login`), then restart",
    "local": "start your local OpenAI-compatible server (LOCAL_MODEL_URL), then restart",
}


def build_app():
    config = BackendConfig.from_env()
    repo = Repository(make_engine(config.database_url))
    memory = RepositoryMappingMemory(repo)
    mapper = build_default_mapper(memory=memory)
    gateway = getattr(mapper, "_gateway", None)
    print(
        f"model provider: {type(mapper).__name__} ({getattr(gateway, 'model_id', 'deterministic')})"
    )
    check = getattr(gateway, "check_credentials", None)
    problem = check() if callable(check) else None
    if problem:
        hint = _FIX_HINT.get(selected_provider(), "check the model settings, then restart")
        print(
            "WARNING: the model is NOT reachable from this process — fills will leave "
            f"unmatched fields empty. {problem}\n  fix: {hint}."
        )
    else:
        print("model credentials: ok")
    return create_app(
        AppState(
            repo=repo,
            auth=DevTokenAuth(),
            documents=DocumentStore(),
            facts=FactStore(),
            mapper=mapper,
            memory=memory,
        )
    )


def main() -> None:
    import uvicorn

    load_dotenv()
    config = BackendConfig.from_env()
    uvicorn.run(build_app(), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
