# VK post images — phase 1

## Current behavior
- Only quick posts targeting VK support optional generated illustration in Growth Studio.
- One explicitly authorized paid image request is made after the ready post text is produced.
- Default picture: portrait 4:5, exported at 1080 x 1350; alternative: square 1:1 at 1080 x 1080. The profile is bound to the saved post and immutable image checkpoint. Source files are PNG inside the private gallery.
- An image attempt is durably recorded before a paid API call. Never silently repeat a paid call after timeout or restart.
- No image means no automatic conversion of an image-post intent into a text-only VK publication. Existing pure-text posts continue unchanged.
- Text and image require review. Neither generation nor upload means publication.

## Design guidance
- Use the finished post, not just a topic keyword, to depict the central situation or practical solution.
- Strong focal point, simple visual hierarchy, phone-readable, specific to the company industry.
- Avoid AI-robot clichés, unverifiable product diagrams, fake screenshots, unsupported claims, embedded text or logos. Apply accurate text/brand assets in deterministic post-processing later.
- The 4:5 size is a ServGo editorial preset, not a technical VK wall requirement.

## VK upload gate (phase 2)
- Current connected publisher credentials are primarily community tokens; real text-only posting is a different capability from uploading photos.
- The official VK API schema and field issue VKCOM/vk-api-schema#242 show that uploading wall photos with community tokens can fail, even with photo permissions. A valid authorized user token is required for the documented upload path. Compatibility of VK ID tokens must be confirmed in a real end-to-end test.
- Secure, group-bound user authorization will be needed before enabling automatic photo publication.
- Upload flow: photos.getWallUploadServer with group ID -> multipart photo upload to validated VK-owned URL -> photos.saveWallPhoto with returned server/photo/hash -> use actual owner_id and photo ID in wall.post attachments together with message, owner_id=-group ID, from_group=1, stable guid.
- Never send only text on a media-intended post. Preserve at-most-once guard and ambiguous outcome reconciliation. Never expose tokens in logs, URLs or browser HTML.
- Add tests for tenant isolation, wrong group, media mismatch, stale approvals, image failure, upload retry, VK errors and duplicate wall.post.

## Technical references
- https://github.com/VKCOM/vk-api-schema/blob/master/photos/methods.json
- https://github.com/VKCOM/vk-api-schema/blob/master/wall/methods.json
- https://github.com/VKCOM/vk-android-sdk/blob/master/samples/app/src/main/java/com/vk/sdk/sample/requests/VKWallPostCommand.kt
- https://github.com/VKCOM/vk-api-schema/issues/242
- https://github.com/gitroomhq/postiz-app/issues/1408
- https://vc.ru/ai/3014228-kak-sozdat-kartinku-dlya-posta

## Image-format review, November 2026
- Primary composition for a single VK feed illustration: 4:5 portrait (1080 x 1350) because it takes 25% more screen height than an equally wide square on mobile. That is a geometry comparison, **not demonstrated higher reach or CTR**.
- Alternative: square 1:1 (1080 x 1080) for reusable media, product cards, multi-photo post sequences or graphics that need a neutral central focal point.
- Wide 3:2 or 16:9 composition only when the actual scene or UI screenshot requires horizontal space. Do not apply 9:16 Story dimensions to regular feed images.
- VK does not require a single pixel resolution for ordinary wall photos. Do not confuse VK Ads image presets and constraints with ordinary organic wall posts.
- The earlier code forcibly resized to 1080 x 1350, risking stretched people or objects, and the Studio gallery rendered 4:5 previews in a 9:16 box. Both were fixed by native image cropping and aspect-aware preview. Use central safe composition because the underlying Gemini provider may return square even when a 4:5 ratio is requested; do not invent content beyond the crop.
- For file encoding, JPEG (high quality) is normally preferable for photographic images, while PNG is useful for sharp typography, diagrams and flat-color art. Avoid unnecessary WebP->PNG expansion. The source asset stays PNG for now; choose an optimized upload encoding during the separate VK photo upload phase, while verifying the exact MIME and bytes delivered.
- Do not copy VK Ads recommendations such as maximum 20% text area as if they were blanket mandatory rules for ordinary community wall posts.

Sources:
- https://vc.ru/ai/3014228-kak-sozdat-kartinku-dlya-posta (trade article, illustrative guidance, not official VK rules)
- https://vc.ru/social/979292-aktualnye-razmery-izobrazhenii-dlya-oformleniya-soobshestva-vkontakte-v-2024-godu (older, includes 3:2 guidance)
- https://habr.com/ru/docs/companies/design/ (editorial quality principles; not VK-specific ratios)
- https://habr.com/ru/articles/1070282/ (cross-posting transport pitfalls; WebP and PNG size inflation)
- https://tapbox.ru/blog/razmery-kartinok-dlya-socsetey-i-max (comparison of organic-post and advertising dimensions)

## Mandatory visual critic (November 2026)
- **Always on for an opt-in VK post image**, independent of the global text critic flag. The critic sees the actual final image bytes together with the finalized post title and body through a multimodal vision endpoint. It does not review only the image prompt.
- One generated image -> exactly one vision analysis request. Approved user-facing budget: one image generation call plus one image critic call. Critic model: existing ClaudeHub `gemini-flash-3.8` vision route if `XAI_API_KEY` is set; otherwise `gpt-4.1-mini` via OpenAI if `OPENAI_API_KEY` is set. No automatic retry or provider fallback.
- Issues are classified `critical` or `minor`, with confidence `high` or `uncertain`. **Only a high-confidence critical issue in a non-style category blocks.** Minor artifacts, illustrative simplification, style preferences and unproven claims about defects do not trigger a paid redraw or publication blocker.
- Truly serious problems: subject entirely unrelated to the key argument, a directly contradicted main claim, visibly invented brands/results, clearly dangerous content, severe geometric/anatomical faults, essential information unreadable or cut off.
- If review fails, times out or returns invalid JSON, the image and post are preserved but `image_state=review_unavailable`; no automatic publication or repeated paid attempt. `vk_image_critique_intent` is committed *before* calling the provider. `vk_image_critique` is a stored audit record with policy, model, title/body fingerprint, image SHA256, detailed issues, and verdict.
- An image may be marked ready only by a matching approved critic record. `verified_vk_image_approval` validates the audit and content fingerprints. If a user or AI revises the text, the image and critic endorsement are invalidated; it must not be published with a stale picture.
- Owner views show the picture together with critique status and concrete severe findings; a blocked image is still reviewable and downloadable. No automated regeneration of rejected pictures is included in this phase.
- Publication remains blocked for posts requesting an image until the separate VK photo-upload authorization and API path is verified; this PR does **not** assert live VK image publishing.

## Verified live-provider canary, 2026-11-21
- Evidence: [GitHub Actions run 44871088437](https://github.com/grunvald91/Ai_growth_os/actions/runs/44871088437); result `LIVE_CANARY_OK image=1080x1350 vision=approved publication_count=0`. The job ended in `success`; 17 preparatory offline tests passed.
- **Two real calls through ClaudeHub** with the repository's existing secret `API`: `gemini-3.1-flash-image` generated the image, and `gemini-flash-3.8` returned an `approved` visual verdict. Export PNG 1080×1350 was verified; image SHA256 prefix: `7c95005607473ca2`.
- The test used fictional company data, separate temporary SQLite, and no public post/VK write. It confirmed `verified_vk_image_approval=true` and `owner_approval_gate=allowed`.
- The image shows the workshop and phone, aligning with the theme, but the phone screen has decorative English words and an invented number despite the image prompt banning lettering. It depicts an incoming rather than already missed call; these are not considered release-blocking in this example, and the critic approved it.
- The successful canary is **not** proof of the model's ability to recognize and block every serious mismatch. Offline tests exercise blocking with scripted model responses. The actual VK image-upload API still has not been integrated.
- The temporarily installed automatic workflow was deleted immediately after success to prevent accidental repeat paid calls. The audited Python canary script remains in `scripts/operations/vk_post_image_live_canary.py`, without an automated trigger.
