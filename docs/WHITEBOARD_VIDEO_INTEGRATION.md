# Whiteboard UI and worker — 2026-10-12

This supersedes the unconnected-pilot scope in WHITEBOARD_VIDEO_RUNBOOK.md and
WHITEBOARD_VIDEO_DEPLOYMENT.md. Historical renderer acceptance results remain valid.

## Owner flow

Growth Studio → company → Create → **Create narrated drawing video**.
Direct company route: `/studio/products/{product_id}/whiteboard`.

1. Enter a topic (8–400 characters) and choose a maximum of 13–25 paid API calls.
2. Saving creates an immutable request and a pending approval, without model calls.
3. Review the topic, models, voice and call limit. Confirm this exact request.
4. Read-only provider readiness checks run before queueing and again in the worker.
5. The persistent worker produces storyboard, three images, drawing regions,
   per-beat Russian narration and local MP4. The status page refreshes every eight
   seconds while queued/running; it does not poll external provider APIs.
6. Inspect the video and download MP4, SRT, PNG cover and timing JSON.
   The video remains a draft requiring human review. Nothing is published.

Authenticated JSON API: `/products/{product_id}/whiteboard/jobs`, then
`/{task_id}`, `/{task_id}/approve` and `/{task_id}/cancel`.
Approval requires `confirmed: true` and the SHA256 `request_digest` shown in review.
Company-scoped accounts, operator auth and the existing production origin/CSRF
guard apply to all routes, including file downloads.

## Execution and costs

The job is stored in existing Task, Artifact, Approval and AgentRun tables;
there is no database migration. Task claim uses an atomic queued→running update.
The request, model IDs, voice and call limit are bound to the persisted approval
by SHA256. Changing the request invalidates that approval.

Every actual stage call checks the approval and job state and reserves one call
in a database ledger before sending. Storyboard uses the configured
ClaudeHub-compatible endpoint with one POST: no retries or model fallback.
OpenRouter media calls also make one POST each. The same checkpoint store keeps
successful stage bytes and blocks unknown/interrupted calls from automatic retry.

**The limit counts API calls, not dollars.** Exact monetary cost is unknown and
the UI says so before confirmation. No dollar spending cap is claimed. There is
no automatic paid test, scheduler launch, auto-publication or blind retry.
Typical two-beat scenes need `1 + 3 + 3 + 6 = 13` calls; the contract maximum is
`1 + 3 + 3 + 18 = 25`. A lower confirmed limit stops before another call.
Overall execution timeout is 20 minutes. Stopped processes leave unknown billing
state; after a 90-second claim grace, an unlocked orphan job is marked failed.
A live worker's filesystem lock prevents recovery from interrupting it.

Canceling prevents subsequent model calls. A call already in flight can still
finish or be billed. Completed stages remain private; cancellation does not
delete or automatically retry them. Interrupted jobs require operator reconciliation.
Storage: `PRODUCTS_ROOT/_whiteboard/{product_id}/{task_id}` in the existing durable
company-products volume. Requests cannot supply filesystem paths. Symlinks and
unlisted download filenames are rejected.

## Configuration and network diagnosis

The global marketing team's models remain unchanged. Explicit separate variables:

```
OPENROUTER_API_KEY=<private value>
WHITEBOARD_STORYBOARD_MODEL=kimi-2.7
WHITEBOARD_IMAGE_MODEL=google/gemini-3.1-flash-image
WHITEBOARD_SPEECH_MODEL=google/gemini-3.8-flash-lite-tts
WHITEBOARD_REGIONS_MODEL=google/gemini-3.1-flash-lite
WHITEBOARD_VOICE=Kore
WHITEBOARD_PROXY_URL=<explicit existing trusted HTTP(S) proxy, or empty for direct>
```

Production direct GET `/api/v1/key` returned HTTP 403 using urllib and HTTPX.
The response was JSON, not HTML: `{"success":false,"error":"Access denied by security policy."}`,
with `Server: cloudflare`. This corrects the prior report's “non-JSON” wording:
its narrow diagnostic parser expected an error object, but the response had
an error string. No specific blocked rule or IP-based cause was established.

The project's already-configured foreign HTTP(S) proxy returned HTTP 200 for
the same key check from the same API container. Whiteboard now has an explicit
separate proxy setting. HTTPS certificate validation remains enabled; only
the OpenRouter adapter/readiness traffic uses this setting. Proxy failure fails
the job; there is no hidden direct fallback, TLS impersonation or certificate bypass.
Credentials and proxy authentication never appear in artifacts, logs or commits.

The image, speech and vision IDs were found in OpenRouter's live public catalogues,
and Kore was listed for the selected TTS model. TTS discovery uses
`GET /api/v1/models?output_modalities=speech`; `/audio/speech/models` is not
the documented catalogue route.

Official interfaces checked:
- https://openrouter.ai/docs/guides/overview/multimodal/image-generation
- https://openrouter.ai/docs/guides/overview/multimodal/tts
- https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key

## Verification

Local Python 3.12: 38 whiteboard tests passed, including the complete authenticated
UI/API→worker→real 30-second MP4 path with synthetic images and tones, cancellation,
approval mutation/digest checks, customer isolation, auth/CSRF, interrupted calls,
call limits, proxy use with TLS verification and no storyboard retry.

The real-provider image/voice quality is not established by synthetic media.
A finished AI video must still be reviewed using the owner's animation process:
https://github.com/grunvald91/video-use/blob/main/skills/grunvald-code-animation/SKILL.md

Deployment validation and final server status are recorded separately after
candidate-image tests and readiness checks. GitHub Actions are not used.
