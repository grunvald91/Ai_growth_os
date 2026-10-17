# Whiteboard UI and worker: production deployment

Completed 2026-10-12 15:00:54 UTC. Rechecked 2026-10-12 18:18:46 UTC (21:18 Moscow).
Implementation commit: [05cddb8](https://github.com/grunvald91/Ai_growth_os/commit/05cddb8fd43350a79235dd39b0891474c1bbb973).
Branch: `wip/whiteboard-video-foundation`.

## Available owner flow

Growth Studio → company → **Создать** → **Рисованное видео с озвучкой**.
Company page: `/studio/products/{product_id}/whiteboard`.

The owner enters a topic and a 13–25 API-call limit, saves a request, reviews the
exact topic/models/voice, and confirms that request. The persistent worker then
produces storyboard, images, drawing regions, narration and MP4. Status,
cancellation, video preview and MP4/SRT/PNG/timing JSON downloads are connected.
Saving a request does not call a paid model. There is no auto-publication.
The limit counts calls, not dollars; monetary cost remains unknown.

## Deployment and verification

Deployed by existing Codespace SSH access to ServGo. API, worker and Telegram
containers were rebuilt from their existing production images with the targeted
application overlay and restarted. PostgreSQL and persistent volumes were preserved.
The release directory remains
`/opt/ai_growth_os/releases/39b11d0e8ad25c6c77a8d84852dc90a8c5ede68d`;
whiteboard integration is an explicit overlay, not a claim that this baseline
commit already contains the feature.

Production candidate acceptance: **108 passed in 52.02 seconds**, Python 3.12,
network disabled. Includes the authenticated form/API → approval → worker →
actual 30-second MP4/download cycle using synthetic images and audio, as well
as authorization, company isolation, CSRF, cancellation, call limits,
approval digest mutation, orphan handling and no automatic model retry.
The log is `/opt/ai_growth_os/whiteboard-releases/05cddb8fd433/acceptance.log`.

Latest read-only production recheck:
- API healthy; worker and Telegram containers running.
- `GET http://127.0.0.1:8091/health` returned `{"status":"ok"}`.
- Live application's generated OpenAPI schema contains the whiteboard HTML and JSON routes.
- Persistent worker code contains the whiteboard job hook.
- Live container provider readiness returned `{"ready":true,"code":"ready"}`.
- Image, speech, vision models and Kore voice matched provider catalogues.
- Studio and whiteboard API reject unauthenticated access (401, deployment check).

Live browser visual QA remains unconfirmed: the cloud browser could not
authenticate to the production Studio. Authentication was kept enabled.
No real paid AI video was generated in this implementation/acceptance session;
synthetic media does not establish actual provider image/voice quality.

## OpenRouter 403

The production direct key GET returned JSON
`{"success":false,"error":"Access denied by security policy."}` (HTTP 403).
The exact blocking rule was not established. The same key check from the same
container returned HTTP 200 through the project's already configured trusted
proxy. Whiteboard uses that connection explicitly via `WHITEBOARD_PROXY_URL`.
Latest deployed readiness passes through this connection. This fixes access for
whiteboard traffic; it does not claim direct server access is now unblocked.
TLS certificate verification remains enabled and no implicit fallback is used.
Credentials and proxy authentication are excluded from this report and Git.

Configured models:
- Storyboard: `kimi-2.7`, existing ClaudeHub-compatible endpoint.
- Images: `google/gemini-3.1-flash-image`.
- Speech: `google/gemini-3.8-flash-lite-tts`, voice `Kore`.
- Regions: `google/gemini-3.1-flash-lite`.

## Operations

GitHub Actions deliberately launched: **0**. The code commit's queried PR workflow
list was empty. No billable-minute telemetry was consumed or inferred.
The documentation-only final report uses `[skip ci]` per AGENTS.md.

Private rollback material and original image tags are retained under
`/opt/ai_growth_os/whiteboard-releases/05cddb8fd433`. Backups include existing env,
compose and changed files; do not expose them. Rollback restores those backups
and original service image tags, then recreates only API/worker/Telegram.
Temporary readiness credentials were removed after the successful runtime check.
Nonsecret deployment/recheck records remain in `status.json` and `recheck-final.json`.

See [integration and limits](WHITEBOARD_VIDEO_INTEGRATION.md) for the execution
contract and network diagnosis.
