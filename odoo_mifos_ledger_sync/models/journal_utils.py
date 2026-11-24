"""Create, post and update Odoo journal entries mirrored from Fineract.

Every function takes an explicit ``env`` so it behaves the same whether it is
called from an HTTP request or from the RabbitMQ consumer thread. Callers own
the transaction: functions raise ``LedgerSyncError`` (or any ORM error) and
the caller rolls back.
"""
import datetime
import logging

from .validation import validate_journal_entry, validate_account_ids, get_currency_id

_logger = logging.getLogger(__name__)

LEDGER_JOURNAL_CODE = 'LS'
LEDGER_JOURNAL_NAME = 'Ledger Sync'
COMPANY_PARAM = 'ledger_sync.company_id'

# Java (Fineract) date pattern tokens -> strptime directives, longest first.
_JAVA_DATE_TOKENS = [
    ('yyyy', '%Y'), ('MMMM', '%B'), ('MMM', '%b'), ('yy', '%y'),
    ('MM', '%m'), ('dd', '%d'),
]
_FALLBACK_DATE_FORMATS = ['%d/%m/%Y', '%d %B %Y', '%Y-%m-%d', '%d-%m-%Y']


class LedgerSyncError(Exception):
    """Payload or reference data problem: the request cannot succeed as sent."""


def resolve_company(env):
    """Company that receives Fineract postings.

    Set the ``ledger_sync.company_id`` system parameter to choose it;
    defaults to the main company.
    """
    param = env['ir.config_parameter'].sudo().get_param(COMPANY_PARAM)
    company = env['res.company'].sudo().browse(int(param)) if param else env.ref('base.main_company')
    if not company.exists():
        raise LedgerSyncError(f"Ledger sync company {param} does not exist")
    return company


def get_ledger_journal(env, company):
    """Return the company's 'Ledger Sync' journal, creating it if needed."""
    Journal = env['account.journal'].sudo().with_company(company)
    journal = Journal.search([('company_id', '=', company.id), ('code', '=', LEDGER_JOURNAL_CODE)], limit=1)
    if not journal:
        journal = Journal.create({
            'name': LEDGER_JOURNAL_NAME,
            'type': 'general',
            'code': LEDGER_JOURNAL_CODE,
            'company_id': company.id,
        })
        _logger.info("Created ledger sync journal %s for company %s", journal.id, company.name)
    return journal


def _java_pattern_to_strptime(pattern):
    for java, py in _JAVA_DATE_TOKENS:
        pattern = pattern.replace(java, py)
    return pattern


def parse_transaction_date(date_str, date_format=None):
    """Parse a Fineract transaction date into a ``datetime.date``.

    Fineract sends ``dateFormat`` (a Java pattern such as ``dd/MM/yyyy``)
    alongside the date; honour it first, then try the common formats.
    """
    if not date_str:
        return None
    formats = [_java_pattern_to_strptime(date_format)] if date_format else []
    formats += [f for f in _FALLBACK_DATE_FORMATS if f not in formats]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _prepare_line_vals(payload, account_map, currency, company):
    """Build account.move.line values from the payload's debits and credits."""
    foreign = currency != company.currency_id
    lines = []
    for side, sign in (('debits', 1), ('credits', -1)):
        for entry in payload.get(side, []):
            # Zero-amount legs are kept (Fineract posts zero-value accruals) so
            # every transaction mirrors 1:1 and Odoo never sees an empty entry.
            amount = float(entry.get('amount') or 0)
            vals ={'account_id': account_map[str(entry['glAccountId'])], 'name': payload.get('comments') or payload['transactionReference']}
            if foreign:
                # Odoo converts amount_currency into company currency at the move date rate.
                vals.update({'currency_id': currency.id, 'amount_currency': sign * amount})
            else:
                vals.update({'debit': amount if sign > 0 else 0.0, 'credit': amount if sign < 0 else 0.0})
            lines.append((0, 0, vals))
    return lines


def _check_payload(env, payload, company):
    """Validate a transaction payload and resolve its references.

    Returns (date, currency, account_map) or raises LedgerSyncError.
    """
    is_valid, error = validate_journal_entry(payload)
    if not is_valid:
        raise LedgerSyncError(f"Invalid payload: {error}")

    trans_date = parse_transaction_date(payload.get('transactionDate'), payload.get('dateFormat'))
    if not trans_date:
        raise LedgerSyncError(f"Invalid transactionDate {payload.get('transactionDate')!r} "
                              f"(dateFormat {payload.get('dateFormat')!r})")

    currency_id = get_currency_id(env, payload.get('currencyCode'))
    if not currency_id:
        raise LedgerSyncError(f"Unknown or inactive currency {payload.get('currencyCode')!r}")
    currency = env['res.currency'].browse(currency_id)

    debits = payload.get('debits') or []
    credits = payload.get('credits') or []
    if not debits or not credits:
        raise LedgerSyncError("Transaction needs at least one debit and one credit")
    total_debit = sum(float(d.get('amount') or 0) for d in debits)
    total_credit = sum(float(c.get('amount') or 0) for c in credits)
    if currency.compare_amounts(total_debit, total_credit) != 0:
        raise LedgerSyncError(f"Unbalanced transaction: debits {total_debit} != credits {total_credit}")

    gl_ids = {d['glAccountId'] for d in debits} | {c['glAccountId'] for c in credits}
    account_map = validate_account_ids(env, gl_ids, company)
    missing = sorted(str(g) for g in gl_ids if str(g) not in account_map)
    if missing:
        raise LedgerSyncError(f"No Odoo account mapped for Fineract GL account(s) {', '.join(missing)}; "
                              f"sync the accounts first")
    return trans_date, currency, account_map


def _post(move):
    """Post a system-synced move.

    Fineract entries are already final in the core ledger, so they bypass the
    human approval workflow on account.move.action_post and use the core
    posting routine, which still enforces balance, lock dates and sequencing.
    """
    if move.state != 'posted':
        move._post(soft=False)


def _find_existing(env, company, trans_ref):
    return env['account.move'].sudo().search([
        ('company_id', '=', company.id),
        ('ref', '=', trans_ref),
        ('state', '!=', 'cancel'),
    ], limit=1)


def _upsert_custom_entry(env, move, payload, trans_date, currency, account_map):
    """Keep the custom.journal.entry audit copy in step with the move."""
    CustomEntry = env['custom.journal.entry'].sudo()
    vals = {
        'branch_id': payload.get('branchId'),
        'transaction_date': trans_date,
        'transaction_reference': payload['transactionReference'],
        'time_stamp': payload.get('timestamp') or payload.get('timeStamp'),
        'comments': payload.get('comments'),
        'journal_id': move.journal_id.id,
        'company_id': move.company_id.id,
        'account_move_id': move.id,
        'currency_id': currency.id,
    }
    entry = CustomEntry.search([('account_move_id', '=', move.id)], limit=1)
    if entry:
        entry.write(vals)
        entry.credit_ids.unlink()
        entry.debit_ids.unlink()
    else:
        entry = CustomEntry.create(vals)
    line_vals = [
        {'journal_entry_id': entry.id, 'gl_account_id': account_map[str(e['glAccountId'])],
         'amount': float(e.get('amount') or 0), 'type': kind}
        for side, kind in (('credits', 'credit'), ('debits', 'debit'))
        for e in payload.get(side, [])
    ]
    env['custom.journal.entry.line'].sudo().create(line_vals)
    return entry


def process_transaction(env, payload):
    """Create and post the Odoo entry for a Fineract transaction.

    Idempotent on transactionReference: a repeat returns the existing move
    (posting it if an earlier attempt left it in draft).
    Returns (move, created).
    """
    company = resolve_company(env)
    env = env(context=dict(env.context, allowed_company_ids=[company.id]))
    trans_date, currency, account_map = _check_payload(env, payload, company)
    trans_ref = payload['transactionReference']

    existing = _find_existing(env, company, trans_ref)
    if existing and existing.state == 'posted':
        _logger.info("Ledger sync: %s already posted as %s", trans_ref, existing.name)
        return existing, False
    if existing:
        # A draft left by an earlier failed attempt (possibly with no lines):
        # rebuild it from this payload, then post.
        _rewrite_and_post(env, existing, payload, trans_date, currency, account_map, company)
        _logger.info("Ledger sync: completed earlier draft %s as %s", trans_ref, existing.name)
        return existing, True

    journal = get_ledger_journal(env, company)
    move = env['account.move'].sudo().with_company(company).create({
        'move_type': 'entry',
        'journal_id': journal.id,
        'company_id': company.id,
        'date': trans_date,
        'ref': trans_ref,
        'narration': payload.get('comments'),
        'currency_id': currency.id,
        'line_ids': _prepare_line_vals(payload, account_map, currency, company),
    })
    _post(move)
    _upsert_custom_entry(env, move, payload, trans_date, currency, account_map)
    _logger.info("Ledger sync: posted %s as %s", trans_ref, move.name)
    return move, True


def update_transaction(env, payload):
    """Replace the lines of an existing synced entry and re-post it.

    Returns the updated move.
    """
    company = resolve_company(env)
    env = env(context=dict(env.context, allowed_company_ids=[company.id]))
    trans_date, currency, account_map = _check_payload(env, payload, company)
    trans_ref = payload['transactionReference']

    move = _find_existing(env, company, trans_ref)
    if not move:
        raise LedgerSyncError(f"Transaction {trans_ref} not found")

    _rewrite_and_post(env, move, payload, trans_date, currency, account_map, company)
    _logger.info("Ledger sync: updated and re-posted %s (%s)", trans_ref, move.name)
    return move


def _rewrite_and_post(env, move, payload, trans_date, currency, account_map, company):
    """Replace a move's header data and lines from the payload, then post it."""
    if move.state == 'posted':
        move.button_draft()
    move.write({
        'date': trans_date,
        'narration': payload.get('comments'),
        'currency_id': currency.id,
        'line_ids': [(5, 0, 0)] + _prepare_line_vals(payload, account_map, currency, company),
    })
    _post(move)
    _upsert_custom_entry(env, move, payload, trans_date, currency, account_map)
