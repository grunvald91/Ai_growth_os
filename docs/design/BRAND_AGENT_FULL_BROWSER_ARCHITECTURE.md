# ServGo Brand Agent — полнофункциональная архитектура с Playwright
Дата: 2026-11-05. Статус: утверждаемая архитектура для реализации, НЕ выпущенный код.

## Решение владельца и границы

- Используем полноценный Chromium + Playwright как **основной** механизм исследования сайтов, а не аварийный внешний fallback.
- Не урезаем функциональность ради текущего VPS: сперва измерение доступной RAM/CPU, затем upgrade/migration инфраструктуры согласно полному стеку.
- Browser Inspector размещается как **собственный сервис ServGo** в Docker Compose/Kubernetes на хосте с подходящими ресурсами; сторонний облачный browser-as-a-service не требуется и не допускается как скрытая зависимость.
- Для анализа дизайна используем оригинальный TypeScript Dembrandt с его прямыми API/экстракторами, лицензия MIT; не переписываем сложные алгоритмы на Python без технической необходимости.
- Раздел «Фирменный стиль» в Studio заменяется чатом с одной нижней строкой: текст, URL, несколько файлов, голос, история, прогресс, три предварительных макета, сообщения-исправления, одно явное утверждение.
- **Не клонировать чужие бренды без прав.** Извлекаем фирменный стиль организации, сайт и материалы которой владелец вправе использовать.

## Архитектура

User (ServGo Studio mobile/PWA)
  -> FastAPI Brand Agent API (SSO, tenant authorization, file ingress, chat, state, approvals)
  -> Python Brand Orchestrator / durable job store (PostgreSQL, existing task/worker pattern)
     -> Internal Browser Inspector (Node.js + Playwright + Chromium + Dembrandt)
     -> Document Visual Analyzer (existing pypdf/Pillow/Poppler + PDF embedded asset pipeline)
     -> Vision/LLM Adapter (existing model router; remote provider API used for inference, NOT remote browser)
     -> Evidence Collector + Brand Token Resolver + Provenance validator
     -> Brand Kit persistence (BrandVersion/BrandAsset/BrandActive)
     -> Design Engine v2 (deterministic rendered PNG + preview + free edit)
  -> history/result/preview streamed or polled back to Studio

Existing Postgres and product-scoped private file storage are retained. One application owner account with multiple companies must not share image/logo assets between companies.

### A. Browser Inspector (new services/brand-inspector)

**Runtime:** Node.js current supported LTS, pinned Playwright + compatible bundled Chromium, direct Dembrandt dependency or documented extractor imports. Package lock and image digests pinned, explicit license notices. No npx live-download at runtime.

**Browser sampling:**
1. Resolve/validate supplied domain via SSRF-safe outbound request policy; allow only permitted public endpoints. All redirects, third-party requests, page open/popup, CSS/font/image requests also pass the egress policy, including browser-initiated requests.
2. Open context, wait for DOM, fonts.ready, network/hydration stabilization with explicit max time; capture homepage + selected relevant internal pages.
3. Capture at desktop and mobile widths, light/dark where page supports it, including above/below fold snapshots. Do not lose mobile typography, sticky navigation or hero image composition.
4. Extract: site brand and logo candidates (header/hero/footer, linked media, SVG and alt), favicons separately; computed CSS & CSS variables; modern OKLCH etc; typography (font-family/weight/size/line height and webfont URLs); color roles (primary, secondary, CTA, background, text, and confidence); spacing/radius/shadows/borders; image crops; gradients; button/CTA design; layout patterns, responsive breakpoints, brand-consistent illustrations and surfaces.
5. Collect original asset bytes where authorized: headers/navigation logos, SVGs, images from srcset and CSS; exact URL, verified MIME, SHA-256, pixel dimensions, file or inferred origin. Never replace origin with model-drawn similarity.
6. Export normalized BrandExtraction JSON with field-level provenance and screenshots; persist raw report and derived result privately.
7. On partial failure, record actual missing signals; do not invent identity or silently switch to another source.

**Security and operational correctness:** use Chromium sandbox with non-root browser user, appropriate seccomp/egress/CPU/memory isolation, ephemeral per-job BrowserContext, close on completion, timeouts, no host mounts of secrets, no DB credentials in Browser Inspector. Internal API accepts only authenticated service-to-service requests; do not expose it publicly.

References: https://github.com/dembrandt/dembrandt and https://playwright.dev/docs/docker ; https://playwright.dev/docs/browser-contexts .

### B. Document + Image Analyzer (in Python job layer)

- Existing pypdf, Poppler, Pillow, OCR; extend with image extraction from PDF, page thumbnails, SVG/vector and file-metadata handling and targeted multimodal pages.
- Save original brandbook and harvested assets; record page numbers, bounding boxes, and source SHA-256.
- True embedded/logo file from PDF retains original binary; screenshot cut-outs are candidates, not verified originals.
- Vision identifies imagery, composition, stylistic motifs, gradients, colors in photos and ambiguous marks; does not declare exact fonts or originals where evidence is insufficient.
- Enforce safety for malicious PDF/SVG, decompression bombs, embedded JavaScript, file sizes and path traversal. Expensive rasterization runs in isolated worker.

### C. Brand Intelligence / Evidence

- Merge source signals in provenance-first typed schema; represent verified vs inferred and confidence per token (not one confidence for whole style).
- Prioritize approved owner resources, uploaded originals, canonical company domain assets, authentic brandbook and only then screenshot interpretation.
- Detect conflicting logos/colors/fonts; choose best-evidenced version or ask one specific question.
- Expand existing BrandStyle contract v1 to versioned v2 tokens **without breaking current approved versions**: original logo + variants, colors + gradients, mapped typography/font rights/fallbacks, responsive/spatial tokens, visuals/illustration style, CTA and card design, composition rhythm, safe areas, template variants.
- Model output is a draft; only existing owner/approver action activates a version.
- Font file usage requires suitable license and Cyrillic glyph coverage; if not, propose explicitly named fallback. Never claim an unavailable licensed font is installed.

### D. Design Engine and editing

- Preserve existing deterministic renderer and immutable revisions, extend to 1080x1920 output and template DSL based on richer brand tokens.
- Support more than 3 *fixed* layouts: create at least 3 coordinated ready-made preview frames (hook, proof/explanation, CTA) with variable layout chosen by agent.
- Proper placement and scale of genuine logo, font choice/metrics, hierarchy, whitespace, image/gradient backgrounds and CSS-like radii/shadows. Favor screenshots as evidence not wholesale page screenshots pasted into Stories.
- Send free chat edits through design-spec diffs and rerender locally, without paid image model calls. Paid background generation only when separately requested/authorized.
- Review of visual previews and exported PNG/ZIP through mobile PWA; explicit versioned approval.

### E. Chat UX and voice

- Full-height mobile feed, bottom composer with one text field, + attachment picker (URLs/PDF/images/logo), microphone with existing VoiceGlow/STT, send.
- One product_id-scoped conversation and job list; show received/processing/final, percentages only if measurable, else named stages.
- Source cards (file, source page), 3 preview thumbnails, source-of-logo and confidence, one-click approve or request changes.
- Existing UI's massive manual Brand Kit form goes under advanced controls only (not landing page).
- No duplicated STT or sign-in/account system.

### F. Persistent API + pipeline

Python endpoints follow PRD v1 (brand-agent/state/messages/files/revise/approve/updates), with expected version and idempotency keys. Durable PostgreSQL tables or reuse task/artifact primitives: brand_agent_session/message/job/source/evidence/snapshot and generated preview references.

Transitions: accepted -> collecting -> browser_inspecting/document_extraction -> visual_analysis -> synthesis -> preview -> draft_ready -> approval/needs_input/failed.

Persist each step with timestamps, errors, source file SHA and model invocation intents. Restart of API/worker must not lose job; prevent duplicate paid model calls after ambiguous result. Background workers do not hold browser memory in FastAPI API process.

### G. Infrastructure (first-class, not temporary external fallback)

**Preferred topology:** same appropriately sized dedicated VPS/host with containers:
- existing API, application worker, PostgreSQL, Telegram, etc;
- **new brand-inspector**: Node.js + Playwright + Chromium + Dembrandt;
- **new brand-document-worker** or dedicated process in Python worker for heavy PDF/vision preparation;
- internal private network and private shared object storage/volume; monitoring per process.

No mandatory external browser account. Existing externally hosted LLM/vision APIs remain model-inference dependencies; this is distinct from delegating browser processing for lack of RAM.

**Initial sizing hypothesis (to test, not provider requirement):** 4 vCPU / 8 GiB RAM and SSD sized for Chromium image, temporary screenshots, files, DB; 16 GiB class if testing reveals parallel jobs or existing workloads need more. Do not falsely assert these numbers are validated. Benchmark on actual ServGo host/container mixture before production. Separate scaling of Browser Inspector possible later without redesign.

Capacity controls are normal reliability engineering, not feature reductions:
- isolated per-job BrowserContext, bounded page count and resource size, max concurrency determined by measurements;
- peak RSS, container memory.high/max, OOMKills, CPU, disk/temp sizes, queue delay, time to completed preview, successful extraction rates;
- on capacity deficit, upgrade host / provision dedicated Browser Inspector with explicit requirements rather than silently downgrade to HTML-only.

**Release:** separate browser image, lockfile, pinned dependency versions, CI unit tests plus targeted browser integration tests; run expensive end-to-end browser checks locally or in a small scheduled batch, don't spend GitHub Actions repeatedly on unneeded full E2E. Validate production smoke in authorized canary company then merge main and deploy only when code is ready and approved.

### H. Tasks / implementation order

1. **BA-INFRA**: measure actual host CPU/RAM and running containers; define target capacity, deployment topology and egress restrictions; add reproducible browser container (Chromium+Node+Dembrandt+Playwright), health endpoint and test harness.
2. **BA-BROWSER**: implement Browser Inspector contract, multi-page/desktop/mobile/dark analysis, logos/assets/screenshots and provenance JSON. Compare against Dembrandt CLI baseline on controlled benchmark sites.
3. **BA-DOCS**: robust PDF/image/brandbook extraction with original binary resources and page-level evidence.
4. **BA-CORE**: durable job orchestrator, company-scoped source storage, intelligent evidence merging, approved/draft brand versioning, schema v2 migration.
5. **BA-DESIGN**: extensible deterministic Story designer 1080x1920, multiple layout variants and font loading/validation.
6. **BA-CHAT**: mobile-first one-composer chat, attachments and voice, progress, previews, revisions, advanced manual controls hidden.
7. **BA-REVIEW**: tenant isolation, safe browsing, idempotency, origin authenticity, visual quality snapshots, memory/performance under contention, production canary, rollout and rollback.

### Acceptance tests

- URL-only: JavaScript SPA renders correctly in Chromium; original logo, CSS colors, real fonts and responsive differences are extracted with provenance.
- Brandbook-only: text and actual embedded assets are recognized; page numbers and sources are preserved.
- Screenshot-only: suggested visual tokens, no fake verified logo.
- Mixed sources: precedence and contradictions resolved transparently.
- Mobile/desktop/dark variants: no incorrect mode mixing.
- Chat and voice commands: same draft, deterministic free rerender.
- Owner approve only: no automatic promotion to active.
- Security: malicious redirect/SSRF including request subresources blocked; browser sandbox and tenant isolation; no leaking cookies between jobs.
- Runtime: real measured peak RSS and peak memory under 1/2 concurrent brand inspections, under concurrent ordinary ServGo workers, no OOM; if target insufficient, larger host provisioned rather than feature removal.
- Prod: one real company source -> correctly selected original logo and brand identity -> 3 previews -> voice edit -> approved style -> 3 created Stories -> PNG/ZIP; user need not fill any settings.
