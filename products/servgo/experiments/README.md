# ServGo experiments

Machine-readable experiment specifications. Each file's `experiment` object matches the
`POST /products/{product_id}/experiments` request body (`ExperimentCreate`).

## EXP-SERVGO-001 — not launch-ready

`EXP-SERVGO-001.json` is intentionally **not launch-ready**: `baseline_summary` is `UNMEASURED`.
Its thresholds are test design, not historical data.

### Before launch

1. Record the real baseline: count attributable ServGo leads and demo requests for the 7 days
   immediately before launch (date range, source, counts). Use `0` only if you verified it.
   Do not estimate.
2. Create the draft experiment from the spec, with the real baseline in `baseline_summary`:

   ```bash
   jq '.experiment | .baseline_summary = "2026-10-01..2026-10-07: <N> attributable leads, <M> demo requests (source: <where counted>)"' \
     products/servgo/experiments/EXP-SERVGO-001.json \
     | curl -sf -X POST "$API/products/$PRODUCT_ID/experiments" -H 'Content-Type: application/json' -d @-
   ```

   If the draft already exists with the `UNMEASURED` baseline, update `baseline_summary` on it instead.
3. Check readiness (read-only, launches nothing):

   ```bash
   curl -sf "$API/products/$PRODUCT_ID/experiments/$EXPERIMENT_ID/preflight"
   ```

   Continue only when `ready` is `true`. The service refuses to move an experiment to
   `running`/`active` while preflight fails.
4. Do the outreach by hand: 20 contacts, UTM campaign `servgo_exp_001`, a manual source field
   for every lead, and at most one follow-up. No automation, scraping, or paid acquisition.
