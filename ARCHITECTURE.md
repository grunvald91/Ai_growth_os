# AI Growth OS — Architecture

## 1. Purpose

AI Growth OS is a control plane for an AI growth team. It turns business objectives into measurable experiments and specialist tasks, records outputs as artifacts, gathers metrics, and feeds evidence back to an AI CMO.

The system is not a collection of independent chatbots. It is one auditable application with specialized agent roles.

## 2. Phase 1 topology

```text
Owner / CEO
    |
    v
FastAPI Control Plane
    |
    +--> CMO Orchestrator
    |       |
    |       +--> Research / PMM
    |       +--> Content / SEO
    |       +--> Distribution
    |       +--> Analytics
    |
    +--> Workflow Engine (simple in-process/service layer)
    |
    +--> PrivacyGateway --> LLMProvider --> Claude API
    |
    +--> Policy / Approval --> ToolGateway --> external systems (later)
    |
    +--> PostgreSQL
```

One deployable application plus PostgreSQL. A worker/scheduler may run as a second process from the same codebase.

## 3. Trust boundaries

### Private Core
Contains:
- database credentials;
- OAuth tokens;
- API keys;
- raw customer identifiers if ever introduced;
- billing settings;
- external account credentials.

### LLM boundary
Only the minimum context required for reasoning crosses this boundary.

`PrivacyGateway` must process outbound context before `LLMProvider`.

### Tool boundary
Agents never call external APIs with credentials directly. They request typed tool operations from `ToolGateway`.

## 4. Main modules

```text
app/
  api/
  core/
  db/
  domain/
  services/
  agents/
  orchestrator/
  workflows/
  llm/
  privacy/
  tools/
  policies/
  skills/
  scheduler/
```

Dependencies should flow inward toward domain/services. Vendor SDK types should be contained in adapter modules.

## 5. LLM provider abstraction

Define an interface such as:

```python
class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse: ...
```

First adapter:
- Anthropic Claude API

Future adapter:
- OpenAI Responses API / GPT-6 Sol

Domain services must not import Anthropic/OpenAI SDK objects.

## 6. Agent model

Agents are configuration + responsibilities + allowed tools + skill selection.

They are not separate servers.

### CMO
Inputs:
- active goal;
- aggregate metrics;
- experiments;
- decisions;
- open tasks;
- approved constraints.

Outputs:
- structured decisions;
- task creation requests;
- experiment proposals.

### Research / PMM
Produces evidence-backed research artifacts and hypotheses.

### Content / SEO
Produces drafts and recommendations. It does not publish.

### Distribution
Transforms approved artifacts into channel-specific delivery requests. Phase 1 uses fake/no-op tools only.

### Analytics
Aggregates facts. It does not make final strategy decisions.

## 7. Domain model

### Product
Stable root entity for goals, experiments, and tasks.

### Goal
Defines measurable desired outcomes and time horizon.

### Task
Unit of delegated work with parent/child relationships.

### AgentRun
Execution record: provider/model, inputs references, output references, token/cost metadata when available, status, timing.

### Artifact
Versioned output of work: research, copy, report, landing-page draft, etc.

### Experiment
Hypothesis + audience + channel + metric + success condition + budget + status + conclusion.

### Decision
CMO decision: KEEP / STOP / SCALE / TEST, linked to evidence.

### Metric
Time-bound numeric observation with source.

### Approval
Human/policy decision over a proposed action.

### ToolCall
Audit record of requested/executed external operation. Never store credentials.

### CostRecord
Tracks LLM/tool/ad spend in normalized form.

### Publication
Tracks approved content delivery lifecycle.

## 8. Queue model

Phase 1 uses PostgreSQL-backed task claiming.

Do not add Redis/Celery until concurrency or delivery semantics require it.

A worker claims queued tasks using transactional row locking. Implementation should be safe for future multiple workers.

## 9. Product context

Stable files:

```text
products/<slug>/
  .claude/product-marketing-context.md
  product.md
  audiences.md
  positioning.md
  brand.md
  competitors.md
  constraints.md
  goals.yaml
```

Dynamic evidence stays in PostgreSQL.

## 10. Skills

SkillLoader resolves a skill name to a single `SKILL.md`.

Example mapping:
- landing_page_copy -> copywriting
- seo_audit -> seo-audit
- campaign_review -> campaign-analytics

Only required skills should be placed into model context.

## 11. Approval policy

Actions have risk levels:

- GREEN — automatic
- YELLOW — user approval required initially
- RED — explicit manual approval always required

Policy evaluation occurs before ToolGateway execution.

## 12. Daily growth review

```text
Scheduler
   -> Analytics snapshot
   -> CMO context assembly
   -> PrivacyGateway
   -> LLMProvider
   -> structured decision validation
   -> Decision records
   -> Task creation
```

Phase 1 MUST NOT perform automatic publishing or ad-account writes.

## 13. Observability

Use structured logs with:
- request_id
- task_id
- agent_run_id
- product_id

Never log prompts wholesale by default. Never log credentials.

## 14. Deployment

Phase 1 Docker Compose:
- api
- worker
- postgres

The API and worker use the same image/codebase.

## 15. First product

ServGo is the initial product used to validate the architecture. Product-specific assumptions must stay in `products/servgo/`, not in generic orchestration code.
