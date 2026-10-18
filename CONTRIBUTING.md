# Contributing

Thanks for looking. Small, focused changes are easiest to review.

## Setup

```bash
uv sync                                   # Python 3.12+, installs dev tools
docker run -d --name devicelab-dev-db -p 55432:5432 \
  -e POSTGRES_USER=devicelab -e POSTGRES_PASSWORD=devicelab -e POSTGRES_DB=devicelab postgres:16-alpine
cd web && npm ci
```

The tests expect Postgres at `localhost:55432`; set `DEVICELAB_TEST_ADMIN_URL` to use another.

## Before you push

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                                # strict
uv run pytest -m "not chaos" --ignore=tests/chaos \
  --cov=devicelab.domain --cov=devicelab.application --cov-fail-under=90
uv run pytest tests/chaos -m chaos         # slower; kills agents and restarts the API
cd web && npm run lint && npm run typecheck && npm test
```

## House rules

- Keep the dependency direction: `domain` imports nothing outward, `application` talks to
  ports, adapters implement them. If a use case needs a new capability, add a protocol.
- No singletons or module-level state in `domain` or `application`; inject it.
- Typed errors with stable codes (`DomainError` subclasses), never bare `Exception`.
- A new workflow step type means: a model in `domain/workflow.py`, a branch in
  `agent/executor.py`, a test for each, and an update to the allow-list docs. Do not add a
  step that runs arbitrary commands.
- Changing the agent protocol: edit `protocol/messages.py`, run
  `uv run python scripts/export_protocol_schema.py`, and bump `PROTOCOL_VERSION` if the change
  is not purely additive. The schema test fails otherwise on purpose.
- Take row locks in the order job, execution, lease, device (see ADR-008).
- Dashboard: strict TypeScript, no `any`, shared primitives live in `web/src/components/ui`.
- Comments explain why, not what.

## Commits

Conventional Commits (`feat(scope): ...`, `fix: ...`). Each commit should pass lint and types.
