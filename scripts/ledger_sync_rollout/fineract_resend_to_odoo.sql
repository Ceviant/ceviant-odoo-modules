-- Make Fineract resend every journal entry to Odoo (run on the Fineract
-- tenant database that feeds Odoo, i.e. the `default` tenant).
--
-- The old Odoo controller answered POST /ledger/transactions with a random
-- "resp-NNNNNN" id after only queueing the message, and the queue was never
-- consumed, so Fineract recorded entries as synced that never reached Odoo.
-- Clearing those ids makes the PostJournalsToOdoo job resend them. The fixed
-- Odoo module is idempotent: entries already posted are acknowledged
-- unchanged, empty/draft leftovers are rebuilt and posted, missing ones are
-- created. Only run this AFTER the fixed odoo_mifos_ledger_sync is deployed.

BEGIN;

SELECT count(*) AS lines_to_resend, count(DISTINCT transaction_id) AS transactions
FROM acc_gl_journal_entry
WHERE odoo_ref_id LIKE 'resp-%';

UPDATE acc_gl_journal_entry
SET odoo_ref_id = NULL
WHERE odoo_ref_id LIKE 'resp-%';

COMMIT;
