"""Uvicorn entrypoint (server extra). `uv run --extra server python -m
agent_backend.api.server`."""

from ..document_intelligence.fact_store import FactStore
from ..document_intelligence.store import DocumentStore
from ..model_gateway.factory import build_default_mapper
from ..persistence.repository import Repository, RepositoryMappingMemory, make_engine
from .app import AppState, create_app
from .auth import DevTokenAuth
from .config import BackendConfig


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
        print(
            "WARNING: the model is NOT reachable from this process — fills will leave "
            f"unmatched fields empty. {problem}\n"
            "  fix: export AWS_PROFILE=dev AWS_REGION=ap-south-1 (and `aws sso login "
            "--profile dev`), then restart."
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

    config = BackendConfig.from_env()
    uvicorn.run(build_app(), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
