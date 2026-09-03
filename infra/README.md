# Infrastructure

Local durable stack for Slice 4+ (plan.md §20). Not needed for Slices 1-3.

```bash
docker compose -f infra/compose.yaml up -d
export DATABASE_URL='postgresql+psycopg://form_agent:form_agent_dev@localhost:5432/form_agent'
export OBJECT_STORE=s3
export S3_ENDPOINT=http://localhost:9000
uv run --extra server python -m agent_backend.api.server
```

With `DATABASE_URL` unset the backend runs on in-memory SQLite (development
and tests). PostgreSQL is exercised by the same repository API; run the
persistence suite against it with `TEST_DATABASE_URL` set.
