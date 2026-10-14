# Critical review remediation — 14 October 2026

Base: `90d3468`. Scope: the five confirmed findings in `app/llm/critic.py` and connected workflows.

**Release gate: keep `AGENT_CRITIC_ENABLED=false`.** None of the changes in this branch enable the optional reviewer or deploy to a server. The acceptance test suite must run in a full checkout before any production activation.

## Fixed in code

1. **P1, invalid author revision:** `second_opinion` can only replace the original if a `validate_revision` callback is provided. The callback executes the Pydantic/domain validation for the connected workflow; otherwise the reviewer is advisory-only. Creative: full frame timing, format/platform and evidence checks. Quick post: Pydantic, Telegram limits, verified-fact IDs and brand guard. Article: stage contract, grounding validator and exact verified-fact source coverage. On rejection the original response is returned, with `rejected_domain_revision` and the review audit.
2. **P1, context truncation:** the reviewer now uses explicit required fields for quick posts, creative and article production. Goal/topic, audience, brief, channel/format and verified facts are never silently dropped. If a required field is missing or full required material does not fit, `skipped_incomplete_context` is returned and no reviewer request is made. No false `no_material_issues` success is emitted.
3. **P2, Anthropic:** the existing messages adapter now serializes the complete `response_schema` (including nested defs, `finding_id`, `decisions`, `updated_output`) into its system message. This is **prompt-level format guidance**, not native constrained decoding; schema and domain checks remain authoritative.
4. **P2, losing findings after revision failure:** accepted reviewer findings remain in `quality_review.findings` on timeouts, invalid author JSON, missing author decisions and rejected domain revisions. Status distinguishes `review_unavailable`, `revision_unavailable`, and `rejected_domain_revision`. The original stays unchanged.
5. **P2, audit:** creative saves separate `agent_quality_review` artifacts referenced by AgentRun, without changing its old content-stage artifact schema. Quick post stores `quality_review` in both checkpoints and a summary in final content. CMO, research department, intake, competitive intelligence, and viral-content research store explicit review artifacts with run references. Article checkpoints and team cycle keep their existing stored reviewer records. Cost details include `extra_usage_by_model` and reviewer status where available. Token totals remain aggregated per stage and do not double-count reviewer usage.

## Not solved by this patch

- A failed API request that never returns usage cannot have its actual token charge reconstructed; the provider invoice remains authoritative.
- A prompt containing factual constraints cannot be proven fully correct without an external evidence/verifier layer. Deterministic validators enforce schema, citations/IDs and workflow rules but do not establish truth of untrusted sources.
- Anthropic schema guidance is not equivalent to native schema enforcement, and requires empirical gateway compatibility tests.
- Real ClaudeHub inference latency, pricing and actual critique usefulness remain unmeasured.
- These changes need actual tests on a complete project checkout. We did **not** trigger GitHub Actions or run paid models.

## Acceptance commands (offline)

```bash
python -m compileall -q app tests
python -m pytest -q tests/test_critic.py tests/test_critic_regressions.py \
  tests/test_content_creative.py tests/test_quick_post.py \
  tests/test_articles.py tests/test_research_department.py \
  tests/test_team_cycle.py tests/test_company_intake.py
python -m pytest -q
git diff --check 90d3468..HEAD
```

Expected behavior for five synthetic reproductions: (1) invalid 30-second storyboard change rejected and original still completes; (2) oversized facts yield a clear skip, while compacted posts retain topic/brief/channel/facts; (3) Anthropic receives the entire JSON contract; (4) author timeout keeps major findings and author decision status; (5) creative and quick-post outputs retain reviewer audit/extra model usage and remain unposted without owner approval.

Production activation also needs a bounded test batch on a real provider with cost/latency metrics, rollback and owner sign-off.
