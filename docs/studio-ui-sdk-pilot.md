# ServGo Studio — UI SDK pilot

Status: **in production for inline post review; calendar date-move pilot in phase 5**, no database migrations.

## Enable and rollback behavior

- Existing baseline before PR #320: `ef20cb0d7b086fa7a6acaf11406846fe04515a3c`.
- UI SDK is now loaded by default on Studio pages that use the dispatcher.
- To test the legacy editor on a page, append `?ui_sdk=0` to that page's URL.
- To return to UI SDK mode, remove that query parameter or use `?ui_sdk=1`.
- This only affects presentation in the dispatcher edit form. The underlying
  API, approvals, executor, user and company scope are unchanged.
- To revert the feature entirely, revert the merge commit for PR #320
  (`84dcf815033dde3660c4e4cb5b686a7a56e65618`) and the subsequent
  default-enabled commit, and redeploy the previous known-good revision.
- Source-code rollback does not replace database backup. No migrations were
  introduced by the pilot.

## What is actually new

Ask dispatcher to prepare a post. Open its pending confirmation with
`Изменить`. The existing edit form now wraps its two optional fields
(`Аудитория` and `Дополнительные требования`) into a native UI SDK
`<ui-collapsible>` section. If optional fields contain values, the section
starts open. Closing it does not clear typed values.

Editing stays on `POST /studio/products/{id}/dispatch/edit` with a server
revision check. Creating or publishing requires a separate explicit
confirmation on the existing dispatcher endpoint.

The SDK is not a replacement for the dispatcher or its one-action-per-turn
execution logic. More UI modules, multi-action orchestration and a full
conversation-driven content editing loop are future work.

## Verification

`studio_ui_sdk` contains pinned package and lock files, bundling commands
and a Chromium test scaffold. The built asset is self-hosted at
`/studio/assets/ui-sdk-pilot.bundle.js`, not loaded from an external CDN.

```sh
cd studio_ui_sdk && npm ci && npm run build
cd ..
python -m pytest -q tests/test_studio_ui_sdk_pilot.py tests/test_dispatcher_api.py
```

Latest earlier verified pilot run:
[GitHub Actions 43709893591](https://github.com/grunvald91/Ai_growth_os/actions/runs/43709893591) —
25 Python tests and Chromium smoke (toggle, keyboard, values, default-open).
The live authenticated Studio and production deployment must be verified
separately from the isolated browser smoke.


## Phase 5 — interactive calendar publication moves

In the existing Studio calendar (`/studio/products/{product_id}/calendar`), the
regular `<details>` / POST form is progressively upgraded to a self-hosted
UI SDK `<ui-collapsible>` when the bundle is available.

1. Open a movable publication and choose a new local date and time.
2. Press **Проверить перенос**. The interface shows **Было → Станет**
   and the editorial plan timezone, without saving anything.
3. Press **Подтвердить перенос** to submit the existing authenticated,
   company-scoped POST. On success, the server returns to the calendar.
4. If another tab or user already moved this slot, the submitted original
   `expected_publish_at` no longer matches the locked plan; the server
   rejects the stale request with HTTP 409 instead of overwriting.
5. Existing server guards still prevent changing published/scheduled posts,
   colliding times and moves outside the horizon.
6. Append `?ui_sdk=0` to the calendar URL to use the legacy native details
   and POST form. Unavailable JavaScript also leaves the original form usable.

This is a **calendar slot move**, not automatic publication or content
regeneration. Other UI SDK modules remain unaffected.

CI: `studio_ui_sdk/calendar-smoke.mjs` checks mobile/desktop, explicit
preview/confirmation, preserved original value and legacy fallback. The
`studio-calendar-ui-sdk.yml` workflow recompiles the pinned bundle and
runs the server calendar and page tests.

## Phase 5 — editing a plan from the dispatcher chat (PR pending)

The next slice adds two explicit, single-action capabilities:

- `edit_plan_package`: change a topic, objective or audience on one *draft* package.
  The dispatcher model receives only bounded, company-specific package keys.
  It previews **Было → Станет** for changed fields and asks for confirmation.
  The existing `update_draft_package` service updates all adaptations only when
  the package is still a draft and no affected material has started. Expected
  original values are compared under the product and plan row locks so stale
  cards cannot overwrite another edit.
- `move_plan_slot`: move one existing slot on the current active plan or,
  if no active plan exists, the latest draft. The model receives bounded
  current slot IDs and schedule. The date is interpreted in the plan timezone;
  the existing `reschedule_slot` service enforces horizon, collisions,
  published/scheduled restrictions, and the original `expected_publish_at`
  under its plan lock.

Both cards render a structured before/after preview in the existing dispatcher.
The already self-hosted UI SDK Collapsible is used when SDK is enabled and
native readable rows remain when `?ui_sdk=0`. The JavaScript does not call a
write endpoint until the user confirms the server-generated card. Company and
SSO role scopes are checked again at confirmation time. No extra LLM call,
publication, regeneration, migration or new workflow executor is introduced.

Test with: `pytest -q tests/test_dispatcher_plan_changes.py tests/test_dispatcher.py tests/test_content_calendar.py`
plus `node studio_ui_sdk/plan-change-smoke.mjs` after installing the pinned
browser-test dependencies. Workflow: `studio-plan-chat-ui-sdk.yml`.

This remains one change per turn. Bulk plan edits, delete/add-package,
reusable task/material widgets and multi-action orchestration with individual
approvals are subsequent slices. No unreviewed model output is authorized to
write to a plan.


## Phase 5 — reusable task and material cards

A single read-only disclosure contract now appears in the dispatcher conversation,
the Studio work list/overview and material library/dashboard. The primary
navigation link remains separate from the details control, with no nested links
or implicit approval. The shared server helper in
`app/api/studio_resource_widgets.py` escapes descriptions, labels and
library filter attributes. Existing Studio renderers provide real, scoped task
and artifact URLs; no model-created links or fabricated progress metrics.

Each card has a native `<details>` fallback. The shared
`resource-widgets.js` enhancer upgrades it to the pinned, self-hosted UI SDK
`ui-collapsible` after that custom element becomes available. If scripts fail
or the URL includes `?ui_sdk=0`, disclosure remains functional as native HTML.
New conversation cards are upgraded dynamically. The SDK does not introduce a
new persistence or permissions path: content approval, retry and task execution
remain on their existing authorized endpoints, outside the disclosure widget.

The existing `/activity.json` polling continues to be authoritative for task
state; this change adds no competing status/publish/approval handler.
Search/filter attributes move with the material card to the outer wrapper so
library filters still hide a complete item, including its details panel.

Verification: `tests/test_studio_resource_widgets.py`,
`tests/test_content_studio.py`, `tests/test_dispatcher.py` and
`studio_ui_sdk/resource-widgets-smoke.mjs`. The workflow
`studio-resource-widgets-ui-sdk.yml` checks mobile/desktop, keyboard
interaction, dynamically added cards, escaped text, native fallback and
reproducibility of the pinned SDK bundle.

Next planned slice: multi-action orchestration with an independent approval,
idempotency key and rollback strategy for each step. No unreviewed model
output may directly call a write endpoint.


### Post-review fixes (resource widgets)

- Growth v4 uses the `body.dark` variables `--card`, `--tint`, `--text`,
  `--muted`, and `--line`. Shared widget colors now inherit these tokens on
  both themes; the classic Studio design continues to use its own palette.
- On Growth v4 routes, do not append resource widget JS/CSS if the original
  server-rendered page already provides the same assets. The widget enhancer
  also guarantees at most one live observer per document.
- Standalone Studio pages load `resource-widgets.js`, which loads the pinned
  SDK *only if enabled and not already present*. `?ui_sdk=0` performs no SDK
  request and keeps native `<details>` usable. The existing dispatcher loads
  its own same-origin SDK bundle and is not duplicated.
- Historic task cards show their status **at creation time** until an associated
  `task_result` message is received. Completion/failure events then reconcile
  the old card, including reloaded conversations and reordered arrival. Only
  the task ID and terminal status are exposed, never internal task parameters.
  For a retry or live progress, the existing authenticated task page remains
  authoritative.
- Browser regression checks now cover dark/light CSS token changes, one SDK
  request, enabled/disabled SDK, native operation without JS, and both arrival
  orders of task completion messages.


## Phase 6 — bounded read-only multi-step dispatcher (first orchestration slice)

The dispatcher now accepts `intent=plan` with **2 or 3 serial actions**.
This is deliberately a small, safe vertical slice: only
`show_work`, `show_review`, `show_plan`, `show_recent_videos`,
`show_material`, and `go_to` are enabled. This allowlist is independent
from the model-selected capability and checked before **any** step runs.
Each entry is also validated against the existing strict parameter schema and
the registry's analytics/no-cost/no-confirmation policy. Unknown, repeated,
invalid, mutating or paid steps cause a **whole-plan preflight refusal**.

The dispatcher executes validated reads sequentially via the existing
capability executors. A failed step stops later reads, and the resulting
`workflow` block records which steps finished, failed or were skipped.
The server generates the status and text, rather than accepting a model's
claims about progress. Existing company/account scope, saved conversation,
retry client keys and server-owned resource links are preserved. An identical
retry replays the stored result; it never re-executes the plan. No new
database tables, external posting, payment, publishing or agent calls.

Both JS-rendered workflows and server-rendered task/material cards share
the `sg-resource-widget` disclosure contract and pinned local UI SDK
enhancer, including native `<details>` fallback and `?ui_sdk=0`.
A workflow step can display validated read-only result tables, links and
review cards; the workflow itself provides no mutation endpoint.
The desktop/mobile browser check is `studio_ui_sdk/workflow-smoke.mjs`.

**Not implemented in this slice:** chains containing draft generation,
publication, edit, email or any other write. They still follow the existing
single-action confirmation flow, with remaining actions offered as individual
chips. Before allowing mutating multi-action plans, implement a durable
step-state store, per-step confirmation/idempotency, concurrency protection,
worker recovery and explicit partial-failure/compensation semantics. Avoid
claiming atomic rollback for delivered messages or published content.

Next UI consumers: unified content review, SEO factory phase progress,
AI Chat specialists, Instagram comment replies, then campaign/BookingBot/Mail
components using each product's frontend adapter.


## Phase 7 — durable multi-step draft workflows (bounded rollout)

The first write-capable orchestration slice accepts **2–3 sequential steps**,
with the explicitly reviewed allowlist:

- Safe reads: `show_work`, `show_review`, `show_plan`,
  `show_recent_videos`, `show_material`, `go_to`.
- Local write: **`create_post` only** — queues a draft for owner review.
  Each such step requires a separate button confirmation; it never publishes.
- No email, external API write, payments, public publication, deletion,
  SEO-factory activation or other side effect is allowed in a chain.

All proposed steps are validated *before* creating the workflow. Two tables
introduced by Alembic revision `0017` persist account/company-scoped
workflow identity, step index, typed parameters, individual approval preview,
step status, task receipt and revision. The model cannot grant approval.

The explicit endpoints are scoped to the authenticated Studio company:

- `POST /studio/products/{id}/dispatch/workflows` — submit a validated plan
  with a client idempotency key. **No task is queued by this request.**
- `GET /studio/products/{id}/dispatch/workflows/{workflow_id}` — get the
  authoritative current status. `GET .../workflows` lists recoverable
  unfinished workflows for the same authenticated account.
- `POST .../workflows/{workflow_id}/decision` — approve/decline **one**
  pending step using its index and current revision. A stale revision returns
  HTTP 409; duplicate approval cannot create a second task.
- `POST .../workflows/{workflow_id}/resume` — run only pending safe reads,
  never auto-confirm a draft. Safe to use after a process restart.
- `POST .../workflows/{workflow_id}/retry-read` — explicitly retry a failed
  read (current revision required). **Failed writes are never retried
  automatically.**

On confirmed `create_post`, the existing `queue_content(commit=False)`
creates the task and its `content_request` artifact in the **same database
transaction** that marks the step completed and stores its task ID. If this
transaction rolls back, neither the task nor the receipt survives. The
workflow row is locked before checking its revision and executing a confirmed
write (PostgreSQL `SELECT ... FOR UPDATE`); the task uses a stable workflow
step identity. No client-supplied task ID, URL or internal tool call is trusted.

The reusable `sg-resource-widget` workflow card renders the explicit
approval outside the disclosure trigger, updates via the owner-scoped status
endpoint, and includes native `details` fallback with `?ui_sdk=0`.
Unfinished workflows are recovered from `/dispatch/state` on a new tab or
topic, even when a chat reply was interrupted after the workflow was stored.

This implements durable *dispatch/queue acknowledgement*, **not** end-to-end
completion of AI-generated content. Completion, approval and publication are
still tracked by existing Task/Artifact systems. These workflows do not claim
rollback of already executed actions.

Before extending the allowlist, each additional write executor must prove
atomic durable receipts or external provider idempotency and compensating
behavior; otherwise the orchestrator must reject it. The next UI consumers
remain unified content review and SEO progress; external dispatch belongs to
a later release after verifying actual provider idempotency.


## Phase 7b — live task lifecycle after durable dispatch

A workflow `completed` status means **every orchestration step was dispatched**,
not that the resulting post has been generated. `GET .../workflows/{id}` now
refreshes a separate, company-scoped view of the linked `Task` rows and
returns `steps[].task_status` and `task_summary`. The post task may be
`queued`, `running`, `waiting_approval`, `completed`, `failed`,
`cancelled`, or `unavailable` when its receipt cannot be resolved in the
same company. Existing task links are only returned when their referenced
task belongs to the authorized company.

The overview `GET .../dispatch/state` and the unfinished-workflows endpoint
continue to include dispatch-completed workflows **while their linked tasks
are queued, running, waiting for review or failed**. They leave the active
overview after the linked tasks reach completed/cancelled (the explicit
workflow GET still returns the receipt). Task status changes require no
workflow mutation/revision increment and cannot trigger any new generation.

The dispatcher card displays current task status separately from the
workflow step status and offers an explicit **«Обновить статус материалов»**
read-only button. On opening saved chat cards, it already reconciles against
the authorized status endpoint. No background paid request, publication,
retry of a failed write, or schema migration is introduced. Browser smoke
covers both the existing two separate confirmations and live status
refresh without replaying either confirmation.


## Phase 7b review fix — distinguish completed generation from owner approval

Quick-post generation saves a `channel_text` Artifact with
`status=awaiting_approval` **then** sets the generating Task to
`completed`. Therefore `Task.status=completed` is not sufficient to mark a
workflow as resolved for its owner.

Workflow snapshots and the active-workflow selector now check both the
company-scoped Task state **and** pending `channel_text` Artifacts for
the confirmed `create_post` step. A completed dispatch stays visible while
its generated post awaits editorial approval, even when the Task is complete;
it leaves the active list after that material is approved. The client gets a
validated, scoped `review_href` to the actual material plus a separate
`task_summary.review_pending` count, without exposing post body, foreign
artifacts, extra write endpoints, or granting approval through the workflow.

The UI distinguishes `"Ждёт проверки"` from `"Задание выполнено"`,
offers a direct link to review (no automatic approval), and reports failures
of user-initiated status refreshes. Read-only refresh does not start an LLM
job or retry any write. Regression tests cover the real
`completed Task + awaiting_approval Artifact` contract, reviewed transition,
foreign artifacts, invalid review links and the mobile/desktop workflow card.


## Phase 8a — unified owner review inbox on the existing Studio route

The existing **`/studio/products/{id}/review`** is the single owner
review inbox; there is deliberately no duplicate approval backend or second
review database. Only `Artifact.status=awaiting_approval` for the
company-scoped review kinds `channel_text`, `article`, `creative_brief`,
`video_script` and `video_render` appears here. This includes SEO articles
once they actually enter owner review; automatically published articles are
not falsely represented as pending.

The inbox now exposes counts for all materials and category filters for
posts, articles (marking SEO articles only when the saved SEO brief is
present), creative briefs plus video, and video alone. The previous
`?kind=text` link is preserved as the text-only union. Both counts and
paginated rows are selected **server-side**, limited to 30 per page (not
a silent truncation of the first 250). The current category, page and
company remain within the same route.

Each item uses the shared `resource_widget(kind="material")` contract
and self-hosted, pinned UI SDK collapsible enhancement. The description
and warnings are generated from real stored metadata. Navigation to the
complete version is a distinct link outside its disclosure control.
With `?ui_sdk=0`, JavaScript disabled, or SDK load failure, native
`<details>` still works. The review page reuses the same resource
CSS/JS as work and the material library.

**Safety invariant:** an inbox excerpt is not a full editorial review, so
the inline one-click approval shortcut was removed from this listing.
Click **«Открыть и проверить»** and use the existing material-detail
approval form, which calls the unchanged `app.content.review.approve_material`
with company scope, fact-check confirmation, validation and version guards.
This does not change publication policy or bypass any approval checks.

Tests cover cross-company isolation, exact counts by kind, paging beyond
30 records, legacy filters, HTML escaping, fact-check refusal, current
navigation, keyboard review disclosure, and UI SDK opt-out. The next
slice is a review-specific approval history and progress summary, without
adding automatic publication or bulk approvals.


## Phase 8b — recorded owner decisions and evidence-based SEO status

The existing authenticated `/studio/products/{id}/review` center gains two
read-only sections, `?section=history` and `?section=seo`. Each section
uses the same owner-scoped product and progressively enhanced shared material
widgets; the native `<details>` fallback remains available.

**Decisions:** The existing `Approval` table is the sole journal. Browser
and dispatcher material approval now write *one* `Approval` event in the
same database transaction as the material status/version change. A repeated
approved request never emits a second event. Browser events are attributed to
the Studio session login; dispatcher events preserve the existing actor,
`dispatcher:approve_material` action and `via` audit field. The history
contains only timestamped, actual decisions with a reviewed company's
matching `Artifact.product_id`, plus decided SEO publishing approvals.
The history explicitly does not backfill historical decisions from a
material's current status, because this would invent a reviewer or date.
No extra approval/decline buttons or release endpoints are added.

**SEO progress:** Items are company-scoped `content_factory` tasks with
`SEO-ARTICLE:` prefixes. The view reads the task status, latest `article`
artifact, whether an `seo_final_review` record exists, and the matching
`Publication(channel=own_site, status=published)` of the *same* article.
A critic record means only that an audit exists, **not** that the current
article version passed; only `Artifact.status=published` together with a
matching confirmed publication counts as published. Conflicting records
are explicitly shown as requiring reconciliation. Task creation and article
approval are never confused with successful publication. Both pages use
server-side pagination (30) and no external calls.

The next production step is to reconcile any stale SEO publication records
using trusted server evidence; never repair by fabricating success status
or automatically republishing from the UI.


## Phase 8c — read-only SEO publication reconciliation and owner attention

The company-scoped `app.seo.diagnostics.audit_seo` reads actual
`content_factory / SEO-ARTICLE:` tasks, current `article` artifacts,
`seo_final_review` records and matching `own_site` publications. It does
not call an external blog, infer indexed search visibility, run the model,
change any database status or attempt a retry.

Issues are separately identified and prioritized:
- Published Article without a matching published Publication receipt, or a
  published receipt while Article is not marked published; contradictory
  publication receipts are treated as needing investigation.
- Failed generation; a running task unchanged for more than **2 hours**.
- A queued SEO job whose planned time and last update are more than **24h**
  in the past; a completed task missing its article for more than **24h**.
- A completed draft or approved article more than **24h** past its due time,
  with no confirmed publication.

These are *diagnostic thresholds*, not proof that a worker is stopped or
that the public page is inaccessible. A future scheduled article is not an
issue, and a post waiting for the owner to approve is not falsely labeled
an error. Reconciliation checks matching company IDs for every related
database query and selects the newest article for a task. It loads IDs in
small batches to avoid oversized `IN` clauses and counts all company tasks
before applying page limits.

`/studio/products/{id}/review?section=seo&view=issues` shows a paginated
list of only the detected issues with exact total and original task/material
links. `?section=seo` still shows all observed stages. Both use the shared
accessible material cards and a native no-JS fallback.

The existing `home-summary.json` adds `seo_issues`,
`seo_issues_scanned` and up to three evidence-backed SEO alerts; the
Growth v4 daily drawer shows a **Проверить SEO** counter only when issues
are present. No unrelated company's task title, receipt or link is emitted.

The older SEO run view, SEO plan calendar and Growth run card can no longer
claim a published article based solely on a saved `/blog/` link. Both
`Artifact.status=published` and a matching `Publication(status=published)`
must agree. A mismatch has a separate, non-retryable label; it never
unlocks a paid "repeat generation" button.

This is a **database-led audit**, not a verified live URL/HTTP probe.
Reconciliation, repair, reindexing and external availability checks remain
separate future tasks, with explicit authorization and controlled execution.
