# CLAUDE.md — AI Growth OS

## Mission

Build AI Growth OS: a safe, auditable modular monolith that orchestrates an AI growth team around products.

The first real product context is ServGo.

## Core architecture rules

1. The CMO/orchestrator delegates specialist work. It must not become a monolithic do-everything agent.
2. Every meaningful action should be represented as a Task, Artifact, Experiment, Decision, Metric, Approval, ToolCall, or CostRecord.
3. Stable product knowledge belongs in versioned product files. Dynamic operational state belongs in PostgreSQL.
4. LLM access MUST be behind an `LLMProvider` abstraction. Do not couple domain code to Anthropic-specific SDK types.
5. Credentials NEVER enter prompts. External credentials are resolved only inside ToolGateway/provider adapters.
6. PrivacyGateway runs before every outbound LLM request and must support redaction/allowlisting.
7. All external write actions pass through policy/approval checks.
8. Prefer a modular monolith. Do not introduce Kubernetes, Redis, Celery, RabbitMQ, Temporal, or microservices in Phase 1.
9. Prefer explicit code and small modules over framework magic.
10. Every important CMO decision must include evidence references. If evidence is absent, store an empty evidence set; never fabricate evidence.

## Phase 1 stack

- Python 3.12+
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- Pydantic v2
- pytest
- Docker Compose
- Anthropic Claude API as the first LLM provider
- Provider interface designed so OpenAI GPT-6 Sol can be added later

## Agents in Phase 1

- CMO
- Research / Product Marketing
- Content / SEO
- Distribution
- Analytics

These are logical agents in one application, not independent services.

## Required domain entities

- Product
- Goal
- Task
- AgentRun
- Artifact
- Decision
- Experiment
- Metric
- Approval
- ToolCall
- CostRecord
- Publication

## Task statuses

- queued
- running
- waiting_approval
- completed
- failed
- cancelled

## Artifact statuses

- draft
- awaiting_approval
- approved
- published
- rejected

## CMO decision types

- KEEP
- STOP
- SCALE
- TEST

## Safety boundaries

GREEN: research, analytics, draft generation.
YELLOW: publishing, email sending, website modifications, ad launch.
RED: budget increases, pricing changes, billing/credential changes, destructive operations.

Policy rules belong in a policy layer, not inside prompts.

## Secrets

Never commit secrets. Read them from environment variables.

The GitHub repository currently exposes the Anthropic API key to GitHub Actions through the repository secret named `API`. The workflow maps it into the official Claude Code Action input. Application runtime code should use conventional environment names such as `ANTHROPIC_API_KEY`, but never contain a literal key.

Do not print secrets in logs. Do not enable full Claude Action output when secrets may be present.

## Skills

Use https://github.com/alirezarezvani/claude-skills as the skill source.

Implement a SkillLoader that loads only the skill required for the current task. Do not bulk-load all skills into model context.

The repository may later vendor or submodule the skill source. Keep SkillLoader path-based and configurable.

## Development workflow

For each phase:
1. inspect current code and docs;
2. make the smallest coherent implementation;
3. add/update tests;
4. run tests;
5. run migration checks when models change;
6. inspect git diff;
7. commit with a clear message.

Do not ask for real credentials to implement interfaces. Use mocks/fakes and document required environment variables.

## First workflow

Implement `daily_growth_review`:

1. Analytics assembles available metrics.
2. CMO receives goal, metrics, active experiments, recent decisions, and open tasks.
3. CMO emits structured KEEP/STOP/SCALE/TEST decisions.
4. New tasks are created when needed.
5. All decisions and evidence references are persisted.
6. No automatic publishing or advertising writes in Phase 1.

Read `ARCHITECTURE.md` and `IMPLEMENTATION_PLAN.md` before coding.

## GitHub Actions budget policy (mandatory)

Before planning or executing any CI, deployment or GitHub Actions operation, read and follow [AGENTS.md](AGENTS.md). Its cost, safety, diagnostics, limits and reporting rules apply equally to Claude Code and any delegated agent. Do not bypass them through issue comments or other automation triggers.

## Code review

Review rules for this repository are in [REVIEW.md](REVIEW.md). Before opening a PR, verify the work (`python -m pytest -q`, migration checks when models change) and, where available, run `/code-review` and `/security-review` locally.

The `AI Review` workflow (`.github/workflows/ai-review.yml`) reviews a PR when it is opened or marked ready, and again when the `ai-review` label is added. It intentionally does not run on every push, to respect the Actions budget policy above. If the reviewer repeats the same finding, fix the cause in REVIEW.md, CLAUDE.md or a test instead of re-running it.
