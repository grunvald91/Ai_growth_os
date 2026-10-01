# EXP-SERVGO-001 operating pack

This directory contains the candidate cohort and outreach operating rules for the first measured ServGo growth experiment.

## Current state

- Experiment exists in production as a `draft`.
- This cohort contains 30 public business candidates in Sochi/Adler.
- No candidate in `cohort.json` has been contacted as part of this experiment.
- Sea SPA is intentionally excluded because contact with it predates the experiment and would contaminate attribution.
- Candidate presence, phone and business fit must be manually rechecked immediately before first contact.

## Hard launch gates

Do not contact anyone until all gates are true:

1. Baseline for the immediately preceding measurement period is recorded in the experiment.
2. The ServGo promo/demo destination is reachable from a normal user browser.
3. The landing/form path persists `experiment_id`, UTM values, referrer where available, and the manually confirmed source.
4. A test submission can be traced end-to-end to EXP-SERVGO-001.
5. The exact first-contact copy has human approval.
6. Each candidate has been manually validated as a relevant service business and a current public contact channel exists.

An external check in the ChatGPT session on 2026-10-01 could not verify `promo.servgo.ru` through its browsing tool. This is not evidence of an outage; verify with the production/browser path before first contact.

## Cohort rule

The experiment requires 30 **validly reached** businesses. A candidate counts only after manual validation. If a candidate is invalid, replace it before launch or mark it excluded and add a replacement with a new lead ID.

Each candidate has a stable `SG001-xxx` lead ID and a unique `utm_content` value.

## Contact limits

- One personalized first contact.
- Maximum one follow-up where appropriate.
- No bulk automation.
- No deceptive identity or fake social proof.
- Stop contacting a business that declines.
- Sending remains behind human approval.

## Qualified lead

Count as `qualified_lead` only when an owner/manager (or a person clearly involved in the decision) shows concrete interest in evaluating ServGo, asks for a demo/price/setup details, or agrees to a follow-up discussion.

A delivery receipt, generic acknowledgement, emoji, "спасибо", or non-decision-maker reply does not qualify.

## Required outcome logging

For every contacted lead record:

- lead_id
- first_contact_at
- channel_used
- reached_valid_contact
- reply_at
- reply_class
- qualified_lead
- demo_requested
- form_submission_id if any
- attribution_verified
- followup_at if used
- final_note

No KEEP / STOP / INCONCLUSIVE decision should be made until the experiment contract conditions are satisfied.
