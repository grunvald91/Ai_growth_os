# ServGo — Product Marketing Context

Status: current marketing context for AI Growth OS. Keep verified product capability separate from product direction and unverified market claims.

## Product

ServGo is an appointment scheduling and booking-management system for small service businesses. Owners can manage appointments manually in a visual calendar even without customer self-booking. A separate online booking flow is available for businesses that want it.

Verified admin-panel workflow:
- visual appointment calendar with day and week views;
- manual new appointment: service, specialist or room, date/time, client name, optional phone and notes;
- appointment details are visible and entries can be rescheduled;
- this is useful without customers booking themselves; a toggle to disable the public booking page is NOT yet verified.
Evidence: booking_bot/web/frontend/src/pages/admin/SchedulePage.tsx (ManualBookingSheet and SchedulePage).

Verified client-facing flow includes:
- business/service selection;
- optional specialist selection;
- date/time-slot selection;
- contact details and confirmation;
- optional prepayment instructions;
- customer can mark payment as sent and attach proof;
- owner manually verifies receipt before the booking is confirmed;
- booking status page and notifications.

Verified client-facing AI chat is available in BookingBot for participating businesses, subject to owner settings:
- it uses the selected business's configured service catalog, descriptions, instructions, durations and prices when present;
- it can answer service-selection questions and check the available appointment slots through business-scoped tools;
- it can prepare a booking for the customer to review; creating the booking requires explicit customer confirmation and applicable business permissions;
- it can hand over the conversation to a human operator;
- it must NOT be described as a licensed professional consultation or as knowing business details that the owner has not entered.
Evidence: booking_bot/web/api/routes/ai.py; booking_bot/web/api/sales/tools.py; booking_bot/web/api/sales/policy.py; booking_bot/web/frontend/src/pages/client/ChatPage.tsx.

## Positioning

Position ServGo first around the owner's operational job: visual schedule, manual client appointments and account of visits, with optional self-service online booking. Marketing/AI attribution remain product direction rather than guaranteed live capabilities.

Working product direction: **ServGo Growth — clients, booking and marketing in one system.**

This is the intended combined-product positioning, not a claim that every marketing/attribution capability is already live inside the booking product. Current content may describe the desired end-to-end customer journey, but must distinguish verified booking capabilities from AI-marketing capabilities still being integrated.

Primary value story:
- a visual owner-operated schedule and manual customer appointments even without public online booking;
- fewer scattered records and easier retrieval of appointment details;
- less manual back-and-forth when online self-booking is useful;
- a clearer path from interest to a selected service/time;
- marketing content built around real customer pains rather than generic posting;
- a future closed measurement loop from content -> lead -> booking -> repeat/revenue.

## Commercial status

Legacy ServGo pricing (9,900 RUB setup; 1,490 RUB/month up to 5 employees; 3,000 RUB/month for 6-15 employees) is **not approved for new marketing materials while the combined ServGo Growth offer is being designed**.

Do not publish a current price until an explicitly approved commercial decision replaces this hold. Never silently reuse legacy pricing.

## Current measurement baseline

As of the initial growth-baseline audit:
- website traffic: UNKNOWN;
- historical marketing leads: UNKNOWN;
- demo requests: UNKNOWN;
- acquisition cost: UNKNOWN;
- source attribution was absent before the new marketing-lead instrumentation;
- production currently contains demo/test businesses, so raw user/business/booking totals must not be treated as acquisition metrics without filtering.

For growth experiments, prefer attributed business leads over raw user counts.

## Initial growth objective

Use ServGo as the first real product to validate AI Growth OS and the ServGo Growth positioning.

The goal is to prove a closed evidence loop:

audience signal -> content hypothesis -> asset -> lead/booking evidence -> decision -> next content.

## SEO editorial segmentation

- For searches about electronic appointment journals, admin calendars, specialist schedules or manual booking, explain the existing admin-panel scenario first; do not assume online self-booking is necessary.
- For customer searches about how to book a service, answer the customer's question; keep software selection for business owners as another article with its own real search query.
- Do not assert that the public booking page can be turned off; this control is not verified. Never fabricate performance numbers, search volume or customer cases.

## Candidate business segments

These are hypotheses to test, not established winners:
- massage studios;
- barbershops;
- beauty/service businesses;
- rentals and appointment-based small businesses.

## Editorial voice

Use confident, concrete, entrepreneurial Russian. Lead with a recognizable operational pain, show the cost of the current workflow, then offer a clearer way to work. Avoid corporate filler and defensive disclaimers in reader-facing copy.

Safety belongs in factual discipline, not in repetitive phrases such as “это не обещание роста” or “это только гипотеза”. When uncertainty is material, state it once and naturally.

Strong hooks and declarative headlines are allowed when they describe:
- a real reader problem;
- a process the article explains;
- a benefit the reader can pursue;
- a verified ServGo capability.

Do not state guaranteed business outcomes, invented customer results, fabricated market statistics or unverified ServGo features.

## Constraints

- Never claim a feature that is not verified in product context.
- Never invent market evidence.
- Separate verified facts, research signals, hypotheses and conclusions internally.
- Do not expose internal evidence bookkeeping in reader-facing copy unless it genuinely helps the reader.
- Viral-video views are a creative/topic signal, not proof of buyer demand or conversion.
- Paid media must remain approval-gated until the policy system is proven.
- Customer personal data and credentials must not be placed into LLM prompts when the task can be completed without them.
