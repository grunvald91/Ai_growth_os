---
name: servgo-ui
description: "Use when implementing, reviewing, or fixing Growth Studio, Content Factory, Mail, SEO Factory, or any ServGo interface so it matches the existing BookingBot customer booking experience, including navigation, mobile UX, tapered active underline, themes, and screenshot validation."
---

# ServGo UI in AI Growth OS

**Canonical UI reference is BookingBot's REAL client booking experience**, not an arbitrary visual design or this Studio's existing overrides.

Before any UI task, read in `grunvald91/booking_bot`:
- `docs/brand/servgo-air.md` (design and customer-flow intent)
- `docs/brand/tokens.json` (semantic tokens, dimensions and typography)
- `web/frontend/src/pages/client/booking-flow.css` (real customer layout and interactions)
- `web/frontend/src/styles/design-system.css` (shared admin patterns, `.ds-bn.active::after` and `.choice-accent::after`)
- `.claude/skills/servgo-ui/SKILL.md` (canonical detailed procedure; do not skip)

In this repository inspect:
- `app/api/assets/servgo-air-tokens.css` — copy of BookingBot semantic `s-*` tokens; avoid divergence.
- `app/api/assets/growth-servgo-air.css` — Growth adapter, not a new brand system.
- The actual Growth route/template/assets and tests for the screen being touched. Trace navigation and event handlers, not just CSS.

## Non-negotiables

1. **Consistent visual language**: Onest, airy white/soft-blue surfaces, dark-theme equivalents, restrained blue accent, matching geometry/spacing/radii, existing icons/logo. No heavy gray outlines, random shadows or competing design systems.
2. **Active underline**: reuse the real BookingBot tapered blue-cyan flowing line; it appears **only under selected/active** navigation or options. Match width, centered placement and pointed tips. Honor `prefers-reduced-motion`; don't substitute a solid strip or pill.
3. **Mobile first**: information above, bottom navigation/actions within reach, real usable scrolling, no bottom overlay covering messages/inputs, safe areas and keyboard. Nothing desktop-only without a mobile route.
4. **Functionality first**: no separate decorative shells or fake actions; preserve auth, owner/company switching, task state, API routes, URL navigation, browser history and back/close. A tab must not close on attempted internal navigation. Check that Mail/SEO/Content/video result links open the correct screens.
5. **Themes and readability**: check both light/dark; real text and icon contrast, proper alignment (including notification bell), no merged labels or clipped cards. Don't copy the client flow's light-only rules into dark Studio.
6. **Render and inspect** at 360px/390px/430px, short phone viewport, >=1024px desktop, real interactions, populated and empty/loading/error states; compare to BookingBot baseline. Screenshots must use synthetic data. Static CSS checks alone do not prove visual correctness.
7. **Run focused tests**, inspect the patch, follow local `AGENTS.md` for CI and permissions, and report exactly which real UI and functional checks passed or could not run.

## Change procedure

- Identify actual route + associated CSS/JS and tests before editing.
- Inventory mismatch against BookingBot and map mismatches to existing semantic tokens/components.
- Implement the smallest compatible change, repairing upstream CSS rather than accumulating override layers; don't modify business logic as part of a visual cleanup.
- Validate click paths (not just visual affordances): Studio↔Mail/SEO/Content, organization switching, open completed video, return/back, form submission.
- Inspect actual screenshots, fix differences, rerun targeted checks. **If the browser or baseline is unavailable: say “visual QA not verified.”**
- Report scope, screenshots reviewed, test outcomes and deployment impact. No implicit production release.

**Source ownership:** BookingBot brand book/tokens/client flow remain the source of truth. When intentionally updating the brand, coordinate the canonical source and the Growth copy in one tracked change; never quietly redefine ServGo Air here.
