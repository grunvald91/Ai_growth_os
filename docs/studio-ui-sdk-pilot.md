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

Next planned slices: editor for plan topics and schedule changes in the
dispatcher chat with explicit confirmation; reusable task/material widgets
in Studio; only then multi-action orchestration with per-step approval,
idempotency and rollback. No unreviewed model output can call the calendar
write endpoint directly.
