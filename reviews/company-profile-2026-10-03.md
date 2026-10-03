# Company Profile / Intake audit — 2026-10-03

Scope: the original Grok contract from 0a432bb2e07e6137b1633139a3eb2580c0c12108 (PR81), and the older outreach approval/import/deployment paths interacting with PR82/83. This is a focused review, not a claim that all project code has been audited.

## Runtime checked first

Read-only SSH verification completed at 14:05 UTC / 17:05 Moscow:
https://github.com/grunvald91/Ai_growth_os/actions/runs/37128421357

- Active release: b6c379679a35adbbd648ca88b77572a8a9fdc81e.
- API /health returned {"status":"ok"}.
- API and PostgreSQL healthy; Telegram and worker running, restart counts zero.
- Company profile/intake routes and source modules absent from running API.
- Company menu absent from running Telegram source.

The latest merged company changes c8e7910e1d6367516a72085a733683fc39a122a7 are therefore not the active server release. Deployment is explicitly triggered from deploy/server (or manually), not main:
https://github.com/grunvald91/Ai_growth_os/blob/c8e7910e1d6367516a72085a733683fc39a122a7/.github/workflows/deploy-server.yml

## P1 — approval IDs can authorize replaced outreach content

OutreachPackageService.prepare reuses an existing pending approval ID by replacing its artifact_id. Its pending lookup is scoped only to product/action, so it can also retarget an approval belonging to another experiment of the same product. decide_approval checks pending status and approval ID, without binding an expected artifact from the button. An owner clicking an older button can approve a changed package they did not review.

Reproduced both same-experiment content changes and two concurrent experiments. This behavior exists in the older server commit too. No message execution occurs in these tests or this audit; current decision approval alone does not send outreach.

Sources:
- https://github.com/grunvald91/Ai_growth_os/blob/b6c379679a35adbbd648ca88b77572a8a9fdc81e/app/services/outreach.py
- https://github.com/grunvald91/Ai_growth_os/blob/c8e7910e1d6367516a72085a733683fc39a122a7/app/control/decisions.py

Recommended correction: immutable approval-to-artifact binding; new artifact requires a new approval ID; supersede only approvals for the same experiment and require an exact reviewed artifact reference when deciding.

## P1 — deployment importer does not handle company readiness rejection

The existing deploy workflow runs apply_prospect_enrichment with --prepare-outreach before starting the updated API/worker. apply_file commits enrichment and, when the cohort is fully resolved with ready contacts, calls OutreachPackageService.prepare without catching CompanyProfileNotReady. The newly enforced company gate correctly rejects an incomplete profile; this uncaught exception terminates the import command and the set -e deployment step.

Reproduced using the real import service, one fully enriched direct-contact prospect, and an incomplete profile. This is an integration gap between the older importer and the newer profile gate; it is not evidence that the isolated importer was broken before the gate existed. Bootstrap already catches the same exception, but the explicit deploy importer does not.

Sources:
- https://github.com/grunvald91/Ai_growth_os/blob/c8e7910e1d6367516a72085a733683fc39a122a7/app/apply_prospect_enrichment.py
- https://github.com/grunvald91/Ai_growth_os/blob/c8e7910e1d6367516a72085a733683fc39a122a7/.github/workflows/deploy-server.yml
- https://github.com/grunvald91/Ai_growth_os/blob/c8e7910e1d6367516a72085a733683fc39a122a7/app/bootstrap.py

Recommended correction: return a structured blocked reason for company readiness while preserving enrichment and completing deployment; do not disable the outreach gate.

## Lower-priority schema validation gaps inherited from Grok

The original and current contract accept missing schema_version by defaulting to 1.0. VerifiedFact accepts an empty/whitespace-only statement. Reproduced in both versions. Current readiness ignores blank approved proof, so this does not demonstrate a readiness bypass.

Source:
https://github.com/grunvald91/Ai_growth_os/blob/0a432bb2e07e6137b1633139a3eb2580c0c12108/app/company_profile.py

Consider requiring the version explicitly at the canonical JSON file boundary and rejecting blank fact statements, while preserving intended legacy migration compatibility.

## Validation evidence

Run from the repository root:
python -m pytest -q tests/audit_reproductions.py tests/audit_grok_original.py

Result: 7 passed: 3 original Grok tests plus 4 reproductions that assert the current undesired behavior. These are audit demonstrations, not acceptance tests claiming the defects are fixed.

Existing focused contract/profile integration/outreach suite: 69 passed, one dependency deprecation warning.

Server verification only read current state. No deployment, canonical profile modification, owner approval, publication or outreach was performed.
