# ServGo Studio dispatcher: capability audit (2026-11-25)

Scope: static audit of the mounted Studio routers in app/main.py and the dispatcher registry/executors. A route in source is not evidence of a working integration in production. The operator console and the separately authorized Mail/Booking modules require separate audits. Do not expose their permissions through a tenant dispatcher.

## Runtime source of truth

- `app/dispatcher/registry.py`: CAPABILITIES are real callable actions; PAGES and PLATFORM_GUIDE are navigation-only guidance. The model receives these separately every turn.
- `app/dispatcher/executors.py`, `materials.py`, `plan_changes.py`, `owner_views.py`: actual handlers. Every action must have an executor or describe/confirm pair, validated params, cost/risk classification, tenant checks and tests.
- `app/dispatcher/policy.py`: policy is enforced server-side; model cannot grant authorization.
- `app/dispatcher/readers.py`: bounded company-scoped context, including task IDs. No raw provider errors or client records in the model snapshot.
- `app/dispatcher/service.py`: dispatch, approvals, tool audit and idempotency. No fabricated success.

## Inventory and remaining integrations

| Area / source router | Direct through dispatcher | Existing UI/API not yet directly connected |
|---|---|---|
| Content creation — content_studio, whiteboard | create_post, create_creative (story, image, script), create_package, whiteboard_video | standalone article, other editor/render actions |
| Tasks — content_studio, content_studio_activity | show_work, list_tasks, show_task, show_review | retry of stopped content job, mark notifications read, download images |
| Video — whiteboard | whiteboard_video, show_recent_videos, cancel_video, repair_video_images | resume checkpoint and budget top-up; video files via UI |
| Materials — content_studio | show_material, approve_material, revise_material | arbitrary material editing, VK image-only regeneration, download |
| Calendar/plan — content_studio | show_plan, make_plan, approve_plan, edit_plan_package, move_plan_slot | pause plan, queue specific package |
| Publishing — content_studio, vk_publishing | schedule_post (Telegram only) | VK schedule, publication cancel/reconcile, channel connection |
| SEO — growth_live | seo_factory; list_tasks/show_task for owner-visible jobs | activation/preferences, queue, retry/cancel individual articles, inspect/search metrics |
| Campaigns — growth_campaigns, growth_live | navigation; recommend_today for recommendations | create/edit/launch/stop/finish campaign, assign resources, measurements |
| Market research — yandex_discovery | navigation to tenant-scoped market-research page | Yandex organization search and saving selected sources; do not conflate with email lead search |
| Competitive intelligence — competitive_intelligence, research_sources | navigation to tenant-scoped market research only | operator-only intelligence, research runs/evidence/export require role-aware integration |
| Comments — comments, comments_instagram | navigation | settings, channels, health, moderation, approve/reply |
| Brand — brand_kit, brand_kit_design, brand_story_editor | navigation | versions, approval, composition, background/library and story editing |
| Company — content_studio_company, company_intake, company_profiles | navigation to profile | intake, company create/edit, destructive actions remain protected |
| Booking/CRM — existing booking bridge | show_schedule, show_insights, fill_open_slots, show_return_candidates, prepare_slot_offer, set_insights_depth, send_to_team | other booking/admin actions require separate booking API audit |
| Team — team_dashboard, tune | navigation to team | model/role configuration and prompt changes, operator-only functions |
| AI chat — ai_chat | navigation | conversations, specialists, files, model requests; not a dispatcher tool yet |
| Accounts — studio_accounts | navigation only; restricted_action for sensitive requests | access, credentials, connection and identity changes remain behind authenticated forms |
| Mail — separately authorized ServGo Mail | no direct dispatcher tools | tenant-specific permissions and actual endpoints for search (Yandex/2GIS), email discovery, personalization, campaigns, sending must be audited before registration |
| Internal/system — health, control, internal_models, voice, studio_login, studio_sso, public_blog, growth_design_preview | no owner mutation tools | infrastructure, auth, public feeds and design preview are not arbitrary model-callable operations |

## Next implementation order

1. Safe task resume/retry and material image regeneration: reuse current services, bind source version/digest and exact task ID; separate paid approval.
2. Publication status, VK scheduling/cancellation and plan pause: typed parameters, exact resource IDs, confirmation and authorization.
3. SEO read-only status/metrics plus explicit action wrappers for individual articles.
4. Market research read/search (quota-aware), then campaign and comments tools; brand tools.
5. Booking/CRM and Mail independent role-aware API audit and adapters; no generic HTTP tool, no arbitrary URLs, no credentials in context.

New tools must not be listed as executable until server handlers and tests exist. Navigation is a temporary capability gap, not proof of execution. Explicitly distinguish missing parameters, unavailable provider, permission denial and unavailable integration. Do not turn a repair request into new video creation.

## Review checklist

- Registry and route keys agree; no invented targets or cross-tenant IDs.
- Prefer direct actions; navigation includes concrete steps; do not claim an action ran when only a page opens.
- Read-only status is based on existing scoped work views and sanitized errors; paid approval remains before calls.
- Repair uses existing checkpoint/approval service and selected model, not new speech generation; canceled dispatcher card leaves unpaid repair draft.
- Verify regression suite and CI before merge/deploy. Static source review is not production verification.
