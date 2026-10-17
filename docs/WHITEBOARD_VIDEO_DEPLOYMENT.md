# ServGo whiteboard deployment — 2026-10-12

## Installed scope

Whiteboard module source: `33b4e966daf2e2607516f2665298fd2a55b6c9e1`.
Deployed as a minimal overlay onto production release
`39b11d0e8ad25c6c77a8d84852dc90a8c5ede68d`; the older branch did not replace
the running application's existing code.

The module is installed in the API, worker and Telegram images. It provides
typed storyboard/asset contracts, provider adapters, stage checkpoints,
the local marker-sweep FFmpeg renderer, key-safe diagnostics and offline rerendering.
No whiteboard HTTP route, Telegram button or registered worker job is included.
The four whiteboard model IDs remain unset; no real AI video was generated.

## Verified on the server

- All 21 whiteboard acceptance tests passed in the candidate API image on Python 3.12.
  Network was disabled; images and audio were synthetic test assets.
- API, worker and Telegram were recreated from the tested overlay images.
  All three run; API health is `{"status":"ok"}`.
- The 121 pre-existing application files retained their digest:
  `dc92a25ae7cf5d9df99e3818fbcb0fddcf67d936d93b88c1f2673316286c4565`.
- Existing persistent volume identities were preserved. PostgreSQL was not restarted.
- `/studio` returns HTTP 401 without credentials.
- Existing ClaudeHub-compatible configuration and its `XAI_API_KEY` were preserved.
  `OPENROUTER_API_KEY` was added privately and is present in all three services.
  No credential values were committed or logged.
- Authenticated read-only OpenRouter `GET /api/v1/key` returned HTTP 200 in Codespaces.
  The same check from the production server returned HTTP 403 with a non-JSON response,
  including a second diagnostic request with a descriptive User-Agent.
  The key works in Codespaces; the reason for rejection from the server is unconfirmed.
  Do not claim production media-provider access or solve this by blindly retrying generation.
- Paid model generation calls: 0. GitHub Actions runs used: 0.

## Deployment and recovery

Private release files and status are under
`/opt/ai_growth_os/whiteboard-releases/33b4e966daf2`.
`deployment-before.json` records original image IDs, service tags and mount identities.
`backup/` retains the prior Compose configuration, private environment and any replaced files.
Old images were also tagged `servgo-whiteboard-base-{service}:33b4e966daf2`.
Candidate images are `servgo-whiteboard-{service}:33b4e966daf2`.
The normal service tags point to those candidates.

Only 15 module/test/documentation files were added to the current release.
Production Compose additionally passes `OPENROUTER_API_KEY` and the four
`WHITEBOARD_*_MODEL` variables into API, worker and Telegram.
These production Compose changes must be carried into the next full release;
a build from an unmerged branch does not preserve this overlay automatically.

Rollback restores the saved files, Compose/environment and original image tags,
then recreates only API, worker and Telegram with `--no-deps --no-build`.
Do not delete or replace the company-products volume.

## Remaining work

Resolve production-server access to OpenRouter through the provider's supported
configuration/support path. Then explicitly select and verify model IDs,
wire persisted authorization and spend limits into a real worker job/UI,
and inspect a complete Russian narrated video before calling the workflow ready.
