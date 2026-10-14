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


## Second review: four follow-up release blockers (base ac553526, 14 October)

These four items were confirmed by the external local reviewer and have now been **changed in source code**, but the following commands must be re-run on the final commit. Previous isolated results of 567 passed/1 skipped applied only to a temporary copy with a single syntax correction, **not** to this branch with follow-up changes.

1. **P0 (import failure):** restored the missing `}` between `metadata={...}` and `validate_revision=...` in `article_production.py`. Added an AST syntax regression check.
2. **P2 (lost TeamCycle restrictions):** large requests for `analytics`, `article_brief`, `video_brief`, `content_plan`, `distribution_plan` require the whole `task`, `task_brief`, `product`, `stable_product_context`, `available_records`, and `context_limits`. If any required context exceeds its budget, return `skipped_incomplete_context`. Unknown task types can no longer silently compact task + request while dropping the actual constraints. Added explicit TeamCycle context retention, oversized skip and unknown-workflow tests.
3. **P2 (editorial planner audit):** every returned planner LLM response is accounted for; where a review exists, an `agent_quality_review` artifact and `AgentRun.output_references` link preserve its status, `findings`, and decisions. Unanswered primary/fallback attempts also generate sanitized attempt records with `provider_timeout`, `provider_error`, or `fallback_error`, unknown-token CostRecord rows and no secret-containing provider error text. The main plan remains gated by owner approval. Added completed-plan/review and provider-timeout integration tests.
4. **P2 (false complete usage):** `critic.py` now preserves `usage=None` when no token report exists. Per-model extra usage is `null` (not `0`) for missing reports and explicitly labelled `usage_known=false`. The cost details helper reads these flags, `usage_unknown_for_failed_calls`, and response usage; all critic-connected core workflows now use the helper, including creative, quick-post, article, calendar and TeamCycle. Zero tokens are distinguishable from missing usage. Added tests for successful criticism with missing usage and genuine zero counts.

### Final offline acceptance gate

```bash
python -m compileall -q app tests
python -c "import app.main; print('app.main import OK')"
python -m pytest -q tests/test_critic.py tests/test_critic_regressions.py \
  tests/test_content_calendar.py tests/test_content_creative.py \
  tests/test_quick_post.py tests/test_articles.py \
  tests/test_research_department.py tests/test_team_cycle.py
python -m pytest -q
git diff --check 90d3468..HEAD
```

Do **not** switch on `AGENT_CRITIC_ENABLED` until the final branch passes the gate and a small, monitored provider-specific pilot is reviewed. Do not conflate an unknown provider charge with a verified zero price. No GitHub Actions jobs or paid model tests were initiated by these changes.
