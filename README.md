# AI Growth OS

AI Growth OS is a modular monolith foundation for a safe, auditable growth operating system. Phase 1A provides the FastAPI health endpoint, PostgreSQL persistence models, and migration/container scaffolding. Phase 1B adds core services and the provider-neutral LLM/privacy boundary. Phase 1C adds in-process agent contracts, single-skill loading, fake tools, and deterministic approval policy. Daily growth review orchestration and production integrations remain out of scope.

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

Application settings are loaded from environment variables or `.env`. Set `APP_ENV` and `DATABASE_URL` for the application database. LLM access is provider-neutral and configured with `LLM_PROVIDER` (default `anthropic`) and `LLM_MODEL` (default `claude-opus-5-5`). The Anthropic adapter reads `ANTHROPIC_API_KEY` and supports an Anthropic-compatible `ANTHROPIC_BASE_URL` such as `https://api.anthropic.com`. Keep credentials out of prompts and never commit them.

## Phase 1C boundaries

Set `SKILLS_ROOT` (default `skills`, relative to the working directory) to a local checkout or vendored copy of [claude-skills](https://github.com/alirezarezvani/claude-skills). Construct `SkillLoader(get_settings().skills_root)` and call `load("relative/skill-directory")` to read exactly `<root>/relative/skill-directory/SKILL.md`. Nothing is downloaded or bulk-loaded. Missing files raise `SkillNotFoundError`; invalid or out-of-root paths raise `SkillLoadError`. Keep the local skill tree trusted and read-only during execution.

`Agent.run(...)` selects one skill, calls `LLMService` once, validates one structured tool proposal against caller-supplied UUID references, checks role capabilities and approval policy, and executes at most one fake tool. See `tests/test_agents.py` for runnable offline integration examples using `FakeLLMProvider`, `PrivacyGateway`, `SkillLoader`, and `FakeToolGateway`.

Agents are logical roles inside this application, not background services. Each invocation is bounded; reasoning uses `LLMService`, which sanitizes requests through `PrivacyGateway` before calling a provider. CMO delegates specialist work; Content / SEO drafts but cannot publish.

Tool requests carry typed operations and UUID resource references, not credentials or arbitrary adapter arguments. Policy is deterministic code: analytics reads, research, drafts, and calculations are GREEN; publishing, email, website changes, and ad launches are YELLOW; budget increases, pricing, destructive data actions, and billing/credential/access changes are RED. YELLOW and RED requests stop at approval-required. Phase 1C does not execute approved writes or wire approval persistence into an orchestration workflow; that future boundary must validate a persisted approval against the exact operation and resources before any real adapter runs.

The fake gateway records calls for tests only. No production tool adapter, autonomous loop, daily growth review, or new database schema is included.

## Project layout

- `app/api` — HTTP routes
- `app/core` — settings
- `app/db` — SQLAlchemy base and sessions
- `app/models` — persistence models
- `app/llm`, `app/privacy` — provider-neutral reasoning and outbound sanitization
- `app/agents` — bounded in-process logical agents and capability contracts
- `app/skills` — safe, on-demand loading of one skill
- `app/tools`, `app/policies` — typed fake tools and deterministic approval gates
- `alembic` — database migrations
- `tests` — offline unit and integration tests
