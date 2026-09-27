# AI Growth OS

AI Growth OS is a modular monolith foundation for a safe, auditable growth operating system. Phase 1A provides the FastAPI health endpoint, PostgreSQL persistence models, and migration/container scaffolding. Agent orchestration and external integrations are intentionally out of scope for this phase.

## Local setup

Requirements: Python 3.12+, Docker, and Docker Compose.

### Run with Docker Compose

```bash
docker compose up --build
```

The API is available at <http://localhost:8000/health>. PostgreSQL is exposed on port `5432` with the local development database `ai_growth` and user/password `ai_growth`.

Apply the initial schema from a shell in the API container:

```bash
docker compose exec api alembic upgrade head
```

### Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --reload
```

Set `DATABASE_URL` in `.env` to a reachable PostgreSQL instance before running migrations:

```bash
alembic upgrade head
```

Run tests with:

```bash
pytest
```

## Configuration

Application settings are loaded from environment variables or `.env`. The foundation currently uses `APP_ENV` and `DATABASE_URL`. Future provider credentials belong in environment variables and are not part of this Phase 1A implementation.

## Project layout

- `app/api` — HTTP routes
- `app/core` — settings
- `app/db` — SQLAlchemy base and sessions
- `app/models` — persistence models
- `alembic` — database migrations
- `tests` — smoke tests
