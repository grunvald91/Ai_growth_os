# AI Growth OS

AI Growth OS is a modular monolith for orchestrating an AI growth/marketing team around one or more products.

Initial target: build a safe, auditable system with an AI CMO orchestrator, specialist agents, product context, experiments, artifacts, approvals, analytics, and tool/privacy gateways.

## Phase 1

- FastAPI
- PostgreSQL
- SQLAlchemy + Alembic
- Anthropic Claude API behind a provider abstraction
- CMO orchestrator
- Research / Product Marketing agent
- Content / SEO agent
- Distribution agent
- Analytics agent
- Skill loader
- PrivacyGateway
- ToolGateway
- Approval policies
- Daily growth review workflow

See `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, and `CLAUDE.md`.
