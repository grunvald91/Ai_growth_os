# VK post images — phase 1

## Current behavior
- Only quick posts targeting VK support optional generated illustration in Growth Studio.
- One explicitly authorized paid image request is made after the ready post text is produced.
- Image: 4:5 PNG, exported at 1080 x 1350, stored per tenant and task, visible in owner-authenticated gallery.
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
