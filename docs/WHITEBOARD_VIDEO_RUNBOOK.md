# Whiteboard pilot in Codespaces

## Current verified scope

Linux Codespaces, local FFmpeg renderer and provider-independent pipeline.
The CLI has no paid generation command, public endpoint or deployment action.
It cannot prove provider access, model availability, account balance or price.
The drawing pilot reveals rectangular regions with horizontal marker sweeps;
it does not reconstruct handwritten strokes or trace upstream contours.

## Diagnose the environment

From `/workspaces/servgo-animator`:

```bash
python -m app.content.whiteboard doctor
```

Only key-presence booleans are displayed. The actual project text adapter reads
`XAI_API_KEY` / `XAI_BASE_URL` (the configured ClaudeHub-compatible adapter), or
`ANTHROPIC_API_KEY` when that provider is selected. Media adapters take an explicit
OpenRouter key from the caller; future worker configuration may read
`OPENROUTER_API_KEY`. Do not paste credentials into chat, source files or commits.
Use private Codespaces environment/secrets. Presence is not an API access test.

Model selection is per video, not a change to the shared marketing team's model:

- `WHITEBOARD_STORYBOARD_MODEL`
- `WHITEBOARD_IMAGE_MODEL`
- `WHITEBOARD_SPEECH_MODEL`
- `WHITEBOARD_REGIONS_MODEL`

These names are diagnostic/configuration conventions; the current CLI does not
construct live providers. Model IDs and voice must first be checked against the
selected provider. Verify PNG output, vertical ratio, Russian speech and pricing. Public image/TTS
catalogue responses and their source URLs are recorded in
`WHITEBOARD_PROVIDER_DISCOVERY.json`; this snapshot is not an authenticated
account check or proof that every advertised parameter works together.

## Keep completed stages

Pass `checkpoints=CheckpointStore(run_directory, request, models=chosen_models)`
to `WhiteboardPipeline`. `chosen_models` has exactly four public-ID keys:
`storyboard`, `image`, `speech`, `regions`. Credentials and provider responses
containing authentication data must never be used as model IDs. The caller must
bind these IDs to its actual adapter configuration; this store does not discover
or attest which upstream provider actually handled a call.

Each run gets its own dedicated private directory on durable, trusted storage.
Do not use the repository root. The request/model fingerprint binds the cache to
that run; output digests detect missing or damaged stages. Directory permission
is 0700 and newly written files are 0600. fcntl locking rejects simultaneous
execution of the same run. Atomic writes and fsync preserve stage markers.
This is Linux/POSIX functionality and is not a global queue or account spend cap.

Completed stages are reused without another provider call. A `running` stage
left by a process crash, or an interrupted/failed stage, blocks automatic retry.
Its billing status remains unknown. Reconcile the actual provider response and
charge before an operator authorizes a new attempt; never delete the marker to
blindly retry. The store does not authorize paid calls, record a known cost,
or replace the required persisted owner approval and spend control.

A successful run normally has one storyboard, three images, three region plans
and one speech clip per beat. Three scenes with two beats give
`1 + 3 + 3 + 6 = 13` model calls; six beats per scene give a maximum of
`1 + 3 + 3 + 18 = 25`, before any future separate research. Those are structural
counts, not pricing or evidence of paid calls in this pilot.

## Re-render without models

After a complete stage bundle exists:

```bash
python -m app.content.whiteboard render \
  --assets /path/to/private-completed-run \
  --output /path/to/existing-output-directory/video.mp4
```

This command reads only the fixed checkpoint manifest/stage names and makes
zero provider calls. Output: MP4, SRT, PNG cover and JSON timing manifest.
It refuses existing output names. Default: 720x1280, 20 fps, 30 seconds.
Narration longer than 30 seconds fails; shorter narration holds the final image
with silence. Aspect/format, overlapping future-beat regions, audio decoding,
stream presence and duration are checked. No external publication occurs.

## QA before a real video is delivered

Follow the owner's Code Animation Director process:
https://github.com/grunvald91/video-use/blob/main/skills/grunvald-code-animation/SKILL.md

Review sources and factual claims, maintain one visual style and a measured beat
timeline, inspect scene boundaries/first/last frames, listen for clipped words
and audio joins, inspect Russian labels at phone size and review subtitle timing.
The automated tests use synthetic tones; they do not prove natural Russian voice
quality, real image quality, marketing effectiveness or production readiness.
Keep design, beats, assets/provenance, QA and recurring mistakes beside each
finished video. Use official brand assets only when verified.

## Acceptance tests

```bash
python -m pytest tests/test_whiteboard.py tests/test_whiteboard_render.py tests/test_whiteboard_checkpoints.py -q --disable-warnings
```

2026-10-12: 21 tests passed in Codespaces (Python 3.14.2). Real local MP4 assembly,
cache reuse, local CLI re-render, corrupted cache, failed/interrupted calls,
concurrent execution and key-safe diagnostics were checked. The full project
suite, Python 3.12, production host capacity and live models were not checked.

No paid models or Actions were needed for these tests.

## Автоматическое продолжение после сбоев и журнал ошибок

- После временного сбоя (`generation_failed`, `interrupted`, `provider_server_error`,
  `openrouter_unavailable`, `storyboard_empty`, `storyboard_truncated`) задание само
  возвращается в очередь после паузы 1, 5, 15, 30, 60 минут. Число попыток задаёт
  `WHITEBOARD_AUTO_RESUME_ATTEMPTS` (0–5, по умолчанию 5, 0 выключает).
- Готовые этапы берутся из чекпоинтов и повторно не оплачиваются. Автоповтор тратит
  только вызовы из лимита, подтверждённого владельцем; каждая попытка записывается
  отдельной строкой `Approval` с `action_type='whiteboard_auto_resume'`.
- Если подтверждённые вызовы закончились, видео доделывается только после явного
  подтверждения владельца на странице задания: «Добавить вызовы и доделать видео»
  (до 10 за раз, до 25 сверх исходного лимита). Это тоже строка `Approval`.
- Ошибки ни в каком случае не теряются:
  - `errors` в состоянии задания (последние 50; видно на странице как «Журнал ошибок»
    и в `GET /products/{id}/whiteboard/jobs/{task_id}`);
  - `products/_whiteboard/<product>/<task>/errors.jsonl` рядом с чекпоинтами;
  - строка `whiteboard_failed {...}` в логе worker.
  В записях только этап, код, число вызовов, тип исключения и HTTP-статус; текст
  ответа провайдера и ключи туда не попадают.
