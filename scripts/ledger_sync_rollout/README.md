# Ledger sync fix and financial statements: rollout

These steps were rehearsed end to end on a restored copy of the UAT Odoo
database and the matching Fineract `default` tenant.

## How modules reach an environment (read first)

Each Odoo pod runs `git pull origin <modules-tag>` from this repo into the
shared modules volume when it starts, and **UAT and production both use
`modules-tag: "18.0"`** (`infra-helm-charts/argo-iac/manifests/clusters/*/default/odoo-config.yaml`).
Merging into `18.0` therefore ships to production on its next pod restart.
To deploy to UAT only, point the UAT `modules-tag` at this branch first, and
merge into `18.0` once production has its own rollout.

Pods don't upgrade modules on their own: the startup script only installs
`INIT_MODULES` on first boot. Run step 1 in the pod, then restart it.

## Before you start

* Back up the Odoo database.
* Get finance sign-off on the account reclassifications in step 3. They're
  listed by the dry run.
* Finance users need the **Show Full Accounting Features** access right
  (`account.group_account_user`) to see Accounting > Reporting > Generic
  Statements and Statement of Cash Flows. After changing a user's groups,
  that user has to log out and back in.

## Steps

1. **Deploy the modules** from this branch, then upgrade and install them in a
   single run. `invoice_multi_approval` has to be upgraded in the same run:
   UAT still holds an older view of it that references a removed field.

       odoo -d <db> --stop-after-init \
         -u odoo_mifos_ledger_sync,base_accounting_kit,invoice_multi_approval,dynamic_accounts_report,advance_cash_flow_statements \
         -i ceviant_cash_flow

2. **Uninstall `custom_journal_entry`**. It's an older copy of the
   ledger sync (the standalone `odoo-mifos-ledger-sync` repo, installed under
   another name). Its controller shadows `/ledger/transactions`. The sync tables
   are kept, because `odoo_mifos_ledger_sync` declares the same models.
   Uninstall it from Apps, or from `odoo shell`:

       env['ir.module.module'].search([('name', '=', 'custom_journal_entry')]).button_immediate_uninstall()

   Then delete the `custom_journal_entry` folder from the modules volume
   (`/mnt/modules/<modules-tag>/`) if it's there, and restart the pod.

3. **Fix the ledger company data.** Do a dry run first, then apply:

       APPLY=0 odoo shell -d <db> --no-http < odoo_data_fixes.py
       APPLY=1 odoo shell -d <db> --no-http < odoo_data_fixes.py

4. **Make Fineract resend everything.** Run `fineract_resend_to_odoo.sql` on
   the Fineract `default` tenant DB, then run (or wait for) the *Post Accounts
   To Odoo* and *Post Journals To Odoo* jobs. Already-posted entries are
   acknowledged unchanged. Empty or draft leftovers are rebuilt from Fineract
   and posted. Missing entries are created.

5. **Check**:
   * `select state, count(*) from account_move where journal_id in (select id from account_journal where code ~ '^(SA|OT|LS)') group by 1;`
     should show only `posted`.
   * Run Accounting > Reporting > Statement of Cash Flows for a full year.
     The statement shows an *Unreconciled difference* box only if something
     is misclassified.
   * Balance Sheet: *Balance Sheet* total line = 0.00.

## Optional hardening

* `ledger_sync.api_key` system parameter: once set, all `/ledger/*`
  endpoints require the header `X-Ledger-Sync-Key: <value>`. Configure
  Fineract to send it first. The endpoints are `auth='public'`.
* `ledger_sync.company_id` system parameter: the company that receives
  Fineract postings (defaults to the main company).
* `LEDGER_SYNC_RABBITMQ_CONSUMER=1` environment variable: starts the
  RabbitMQ consumer. It isn't needed, because Fineract posts over HTTP.

## Known data issues for finance (not changed by these scripts)

* Some bank and cash accounts have credit (overdrawn) book balances, for example
  Providus Bank, Cash Control Accounts and Cash on Hand.
* `20206 Accum' Depreciation Office Computers` has a **debit** balance.
* Fineract `508 Term deposits` (a liability) has a debit balance.
* Account codes of Fineract mirror accounts are Fineract's internal GL ids
  (e.g. `505`), not GL codes (`30100`). The names match Fineract exactly.
