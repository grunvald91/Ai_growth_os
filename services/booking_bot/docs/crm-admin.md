# ServGo CRM — BookingBot admin panel

## Scope

The new `/admin/contacts` screen uses the existing `AdminShell` and ServGo design tokens.
It displays contacts from booking history and authenticated (`user:<id>`) AI-sales
conversations within the selected business. Anonymous chat sessions without a
verified user identity remain in "Диалоги" and are NOT silently converted into
people records. For authenticated chat-only visitors, phone/email from their
global account are NOT exposed to the business; contact fields are shown only
when explicitly stored for this business or after a booking.
An owner may add other contacts manually.

Per-business notes, contact-name/phone/email overrides, stage
(`new/active/vip/paused`) and follow-up date are stored in `crm_contacts`.
User account data remains unchanged. The list supports search, filters and
50-item pages. The detail shows the most recent 100 bookings and accurate
all-time counts for one business only. Booking price estimates do NOT imply
payment and are not presented as revenue.

When authenticated accounts are joined, CRM cards move to the surviving user
within each business. If both users have a card in the same business, missing
fields are filled and distinct notes are combined. Conflicting non-empty names,
phones, emails, non-default stages or follow-up dates require reconciliation
before the accounts can be joined; no value is silently overwritten. Combined
notes must fit the existing 5000-character editor limit. These conflicts leave
the accounts separate and preserve both CRM cards.

**MVP constraints:** Manually created contacts and booking users are not
automatically merged based on matching phone numbers; shared phones are not
proof of identity. Due follow-up dates are visible in the CRM filter but do
not send automatic reminders. The list aggregates users in memory and should
be optimized for high-volume tenants before marketing this as a large CRM.

## Verification before production

Use an existing Codespace or trusted local terminal. No GitHub Actions are
needed to run these commands:

```bash
python -m pip install -r requirements.txt
python -m pip install aiosqlite pytest
python -m pytest -q tests/test_crm_api.py
python -m compileall -q database/models.py web/api/routes/crm.py web/api/main.py
cd web/frontend
npm ci --legacy-peer-deps
npm run build
```

Check mobile and desktop rendering, a user with bookings in two businesses,
manual contact create/edit/delete, and account-based AI chat without a
booking. Verify stage/follow-up filters and that no data from other
businesses appears in list, detail or search.

## Migration and deployment order

1. Back up the database and confirm the target environment and connection.
2. Apply `migrations/add_crm_contacts.sql` **before** starting the updated API.
   It adds an independent table and does not rewrite existing users/bookings.
3. Deploy the updated Python model, route and main API registration together.
4. Build and publish the updated frontend. Confirm `/admin/contacts` loads
   with authenticated owner permissions.
5. Check API errors, database logs, and test at least two tenant identities.

Rollback: restore the previous API/frontend build. The additive
`crm_contacts` table can safely remain unused until the problem is
diagnosed; do not drop CRM data casually.

**Status:** Source and test cases were committed on
`feature/servgo-light-crm`; the API migration, deployment, frontend build
and live integration tests have NOT been executed by the authoring session.
