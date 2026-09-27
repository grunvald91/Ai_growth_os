# AI Growth OS — Implementation Plan

## Phase 0 — Repository bootstrap

Deliver:
- project instructions;
- architecture;
- implementation plan;
- secret-safe GitHub workflow;
- .gitignore and .env.example.

Exit criteria:
- repository contains no credentials;
- Claude Code can be triggered from an issue/comment after GitHub App setup.

## Phase 1 — Foundation

### 1. Project skeleton
Create:
- FastAPI application;
- settings/config;
- Dockerfile;
- docker-compose.yml;
- PostgreSQL service;
- pytest configuration.

### 2. Persistence
Implement SQLAlchemy models + Alembic migrations for:
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

### 3. Core services
Implement:
- TaskService
- ArtifactService
- ExperimentService
- DecisionService
- ApprovalService

### 4. LLM boundary
Implement:
- LLMProvider protocol
- LLMRequest/LLMResponse schemas
- AnthropicProvider
- FakeLLMProvider for tests

Do not expose credentials to agent/domain layers.

### 5. PrivacyGateway
Implement:
- secret-pattern blocking/redaction;
- configurable PII redaction hooks;
- allowlisted context assembly;
- tests proving common secret patterns do not reach provider payloads.

### 6. Skills
Implement:
- SkillLoader
- configurable skills root
- single-skill loading
- safe failure if skill missing
- tests

### 7. Agent contracts
Implement typed interfaces/configs for:
- CMO
- ResearchPMM
- ContentSEO
- Distribution
- Analytics

Do not add autonomous infinite loops.

### 8. ToolGateway
Implement typed interface and FakeToolGateway only.

Writes must be gated by policy.

### 9. Policy / approvals
Implement GREEN/YELLOW/RED evaluation outside prompts.

### 10. Daily growth review
Implement a complete vertical slice using fake metrics and FakeLLMProvider in tests.

Inputs:
- goal
- metrics
- active experiments
- recent decisions
- open tasks

Outputs:
- validated KEEP/STOP/SCALE/TEST decisions
- optional task creation

### 11. API
Expose minimal endpoints:
- health
- products
- goals
- tasks
- experiments
- artifacts
- approvals
- trigger daily growth review

### 12. Tests
Required:
- service tests
- model/migration smoke test
- PrivacyGateway tests
- SkillLoader tests
- policy tests
- daily_growth_review end-to-end service test

## Phase 1 exit criteria

- `docker compose up` boots API + PostgreSQL.
- migrations apply cleanly.
- test suite passes.
- daily growth review persists decision records.
- no automatic external writes.
- no secret can be found in repository.
- README includes local setup.

## Phase 2 — ServGo instrumentation

- add ServGo product context;
- connect read-only product analytics;
- define funnel events;
- introduce real Analytics adapter;
- keep external writes disabled.

## Phase 3 — Content loop

- research -> content task -> artifact -> approval -> distribution request;
- first Telegram adapter;
- publishing remains YELLOW approval.

## Phase 4 — Search and web analytics

- GA4 adapter;
- Search Console adapter;
- evidence ingestion;
- SEO/AEO workflows.

## Phase 5 — Paid acquisition

Only after the audit/approval layer is proven:
- ad account read adapters;
- draft creation;
- launch behind YELLOW;
- budget increases RED;
- hard daily/monthly budget caps.

## Development constraints

- Small coherent commits.
- Tests before moving to next phase.
- No real credentials in code/tests/docs.
- No premature distributed infrastructure.
- No fabricated evidence.
