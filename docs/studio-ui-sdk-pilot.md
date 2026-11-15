# ServGo Studio — UI SDK pilot

Status: **merged, enabled by default for testing**, no database migrations.

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


## End-to-end post review in the chat (15 November 2026)

The Studio dispatcher now enhances **awaiting-approval text posts only** with a
UI SDK Collapsible showing the full body from the company-scoped artifact.
The old short excerpt and "Open full view" link still work when the SDK
is disabled with `?ui_sdk=0`.

To exercise the scenario:
1. In Studio for a company, ask "Подготовь пост про ...". Confirm preparation.
2. When the content worker finishes, the conversation shows a material card.
   Open the full post inline; the copy is taken from the actual saved artifact.
3. Select a fragment with mouse, keyboard or native mobile text selection.
   The selection is shown below the body. Enter a concrete correction and
   press "Предложить правку".
4. The dispatcher displays its normal confirmation card: inspect the selected
   fragment and your instructions, then press "Доработать".
5. The existing `content_editor` queue generates a **new immutable version**.
   It appears in Studio through the usual dispatcher update polling, with
   its own full text and approval chip.
6. Press "Утвердить" on that new version and confirm the decision.
   This approves the material; it **does not publish it or schedule delivery**.

Safety and data consistency:
- Neither the preview nor submitting feedback triggers generation/publication.
- The server checks tenant membership through existing Studio auth/CSRF,
  artifact company scope, expected artifact version, valid selection in the
  actual saved post body, feedback length and queue policy.
- Sending feedback uses a stable client key, so a mobile retry does not
  create a second command; executing a revision requires separate confirmation.
- Stale versions and selections are rejected with a readable response.
- Full text is rendered as `textContent`, never model-provided HTML.
- Previous post revisions remain immutable; the revised post is a separate artifact.
- Very long posts (>14,000 characters) retain the original full-detail link
  rather than appearing as a misleading truncated inline version.
- The existing UI SDK bundle is self-hosted. No new database migrations.

Rollback: `?ui_sdk=0` disables the new inline preview and review controls
without changing the server's existing revision/approval APIs. To undo the
server contract changes, revert the feature merge and redeploy a verified
previous revision.
