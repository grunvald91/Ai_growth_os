# ServGo — Product Marketing Context

Status: initial context for AI Growth OS. Keep factual product information separate from unverified market hypotheses.

## Product

ServGo is an online service booking/reservation system for small service businesses.

Current known client flow includes:
- business/service selection;
- optional specialist selection;
- date/time-slot selection;
- contact details and confirmation;
- optional prepayment instructions;
- customer can mark payment as sent and attach proof;
- owner manually verifies receipt before the booking is confirmed;
- booking status page and notifications.

## Verified commercial terms

Current agreed ServGo commercial terms:
- implementation/setup: 9,900 RUB one-time;
- monthly service: 1,490 RUB for up to 5 employees;
- monthly service: 3,000 RUB for 6 to 15 employees.

Treat older promotional pricing as superseded unless a new explicitly approved experiment defines a temporary offer.

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

Use ServGo as the first real product to validate AI Growth OS.

The goal of Phase 1 is NOT to maximize traffic. It is to prove a closed evidence loop:

hypothesis -> task -> artifact/action -> measured outcome -> decision -> next task.

## Candidate business segments

These are hypotheses to test, not established facts:
- massage studios;
- barbershops;
- beauty/service businesses;
- rentals and appointment-based small businesses.

## Constraints

- Never claim a feature that is not verified in product context.
- Never invent market evidence.
- Separate facts, hypotheses, and conclusions.
- Paid media must remain approval-gated until the policy system is proven.
- Customer personal data and credentials must not be placed into LLM prompts when the task can be completed without them.
