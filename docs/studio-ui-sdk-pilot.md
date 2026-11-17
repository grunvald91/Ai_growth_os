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
