"""Uvicorn entrypoint (server extra). `uv run --extra server python -m
agent_backend.api.server`."""

from ..document_intelligence.fact_store import FactStore
from ..document_intelligence.store import DocumentStore
from ..persistence.repository import Repository, make_engine
from .app import AppState, create_app
from .auth import DevTokenAuth
from .config import BackendConfig


def build_app():
    config = BackendConfig.from_env()
    mapper = build_default_mapper()
    print(
        f"model provider: {type(mapper).__name__} "
        f"({getattr(getattr(mapper, '_gateway', None), 'model_id', 'deterministic')})"
    )
    return create_app(
        AppState(
            repo=Repository(make_engine(config.database_url)),
            auth=DevTokenAuth(),
            documents=DocumentStore(),
            facts=FactStore(),
            mapper=mapper,
        )
    )


def main() -> None:
    import uvicorn

    config = BackendConfig.from_env()
    uvicorn.run(build_app(), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
