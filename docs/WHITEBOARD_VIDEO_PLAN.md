# ServGo Growth OS — Whiteboard video integration plan

Status: foundation + independently implemented CPU marker-sweep renderer.
A real MP4 was verified with synthetic images/audio; no paid API calls or deployment.
Branch: `wip/whiteboard-video-foundation`.

## Verified integration points

- Existing FastAPI/Python 3.12 monolith, PostgreSQL Task/Artifact/Approval.
- Existing `app/content/render.py` makes vertical **silent typography cards**;
  it is a separate mode and must remain unchanged.
- Existing content worker supports `content_factory` and `content_renderer` only.
- `app/llm/service.py` sanitizes prompts through PrivacyGateway.
- FFmpeg is already installed by the project's Dockerfile.
- The original Animator fork does not currently publish a LICENSE file in its root.
  Do not copy its drawing engine into this commercial project without clearing rights.

## Proposed mode

`whiteboard_marker`: Russian-language, 30 seconds, 3 scenes, vertical 9:16.
User flow: topic -> draft scenario -> owner review -> explicit cost approval ->
generate images, speech and regions -> draw/synchronize -> preview -> approve ->
export MP4. Never aut​opublish.

## Foundation included in this branch

- `app/content/whiteboard/contracts.py`: strictly validated request,
  storyboard, narration beats, normalized regions, assets and output.
- `pipeline.py`: vendor-neutral, bounded scene-by-scene stage orchestration.
  Audio is generated **per beat**: duration boundaries can be taken from each
  MP3 file by ffprobe instead of guessed speech pauses.
- `adapters.py`: existing LLMService for ClaudeHub text, documented OpenRouter
  image, TTS, image-understanding endpoints. Models/voices remain configurable;
  no assumption that a provider supports every model or 9:16.
- `queue.py`: creates a waiting-approval Task, request Artifact, pending
  Approval; it is NOT exposed by HTTP and NOT handled by the current worker.

## Remaining before paid tests

1. Confirm provider model IDs, voice IDs, supported image formats and
   9:16 capability with live model discovery; probe HTTP schemas without
   committing secrets. In particular ClaudeHub image API shape is not verified.
2. Renderer adapter added: `MarkerSweepRenderer`. It decodes each MP3 to 24 kHz
   mono PCM and counts samples for exact beat boundaries, reveals rectangular
   regions in strokes, and verifies final streams/duration with ffprobe.
   This pilot is not upstream contour tracing/handwriting reconstruction.
3. Renderer uses bounded temporary files beside the destination and removes them
   on success/failure. Complete MP4/SRT/PNG/JSON files are published without
   overwrites using same-filesystem hard links. Multi-file publication is not a
   crash-atomic transaction; job-level crash recovery remains to implement.
4. Implement persisted job progress, retries, cancel, idempotency, authenticated
   owner-only endpoints, explicit per-job API spend cap, and CostRecord audit.
5. Integrate mode selector, review step and playback in existing studio CSS.
6. Run local tests (no Actions), then a single owner-authorized paid run.

## Security / deployment

- Until end-to-end authentication, **do not add public generate endpoints**.
  The web edge protection documented by the studio is not an API authentication
  guarantee.
- Keep all tokens server-side, out of Task/Artifact and log payloads.
- No automatic retry of paid stages without idempotency/cost control.
- No GPU required by upstream Animator's documented CPU drawing engine,
  but actual CPU/RAM workload on the 2 GB host is unmeasured; use an isolated
  Codespace or video worker for the pilot.
- License review and infrastructure measurements before production deployment.

## References

- https://github.com/ashishjamarkattel/Animator
- https://openrouter.ai/docs/guides/overview/multimodal/image-generation
- https://openrouter.ai/docs/guides/overview/multimodal/tts
- https://openrouter.ai/docs/guides/overview/multimodal/image-understanding

## Codespaces validation — 2026-10-12

- Existing Codespace: `silver-space-winner-r467jjq6q695cxr77`.
- Isolated worktree: `/workspaces/servgo-animator`; existing workspace preserved.
- Runtime observed: Python 3.14.2, Pillow 12.3.0; FFmpeg/FFprobe installed.
- Command: `python -m pytest tests/test_whiteboard.py tests/test_whiteboard_render.py -q --disable-warnings`.
- 13 tests passed, including real H.264/AAC 9:16 MP4 at 180x320, 30 seconds,
  subtitles, cover, measured beat timeline, no future-beat reveal, overlap/corrupt
  audio rejection, and >30-second narration rejection without output.
- Production default is 720x1280 at 20 fps; full-resolution throughput, peak
  memory on the 2 GB server, Python 3.12 and real Russian TTS remain unverified.
- Short narration keeps the final image and silence to reach 30 seconds; long
  narration fails rather than being accelerated or cut.
- Renderer async entry point uses a thread; cancellation is not implemented.
- Expected provider key variables were absent; `.env` was absent in the existing
  Growth OS workspace. Secret values were not printed or requested in chat.
- No GitHub Actions were manually started; no production changes.
- Image adapter requests PNG explicitly. Validate the selected model's supported
  parameters before using it; the adapter does not perform model discovery.

### Direct local rendering of prepared assets

Pass three `WhiteboardSceneAssets` instances and a validated `WhiteboardRequest`
to `MarkerSweepRenderer().render(...)` from an async worker, or `render_sync(...)`
from a dedicated CPU process. Use an existing product-owned output directory.
This local renderer makes no model calls and does not authorize the paid pipeline.
The paid pipeline still requires verified owner approval and must remain unexposed
until persisted authorization, spend control and authenticated owner routes exist.
