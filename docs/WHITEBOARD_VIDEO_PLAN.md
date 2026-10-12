# ServGo Growth OS — Whiteboard video integration plan

Status: foundation only; **no generated videos, paid API requests, or deployment**.
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
2. Add a renderer adapter, licensed drawing engine or independent algorithm;
   measure MP3 duration per beat with ffprobe; ensure chronological animation.
3. Put generated assets in bounded temporary storage under products_root;
   atomic final MP4 and covers, subtitles, clean failed-job leftovers.
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
