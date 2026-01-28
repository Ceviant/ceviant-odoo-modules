import logging
import datetime
from odoo import http
from odoo.http import request
from .validation import validate_journal_entry, validate_account_ids, get_currency_id

_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


def get_company_id(env):
    """Retrieve the company_id for the current user in Odoo."""
    user = env.user
    company_id = user.company_id.id
    return company_id


def create_or_get_ledger_sync_journal(env, company_id):
    """Check if 'Ledger Sync' journal exists, otherwise create it."""
    journal_name = 'Ledger Sync'
    journal_code = 'LEDGE'

    existing_journal = env['account.journal'].search([
        ('code', '=', journal_code),
        ('company_id', '=', company_id)
    ], limit=1)

    if existing_journal:
        _logger.info(f"'Ledger Sync' journal already exists with ID {existing_journal[0].id}")
        return existing_journal[0]

    journal_data = {
        'name': journal_name,
        'type': 'general',
        'code': journal_code,
        'company_id': company_id,
    }

    try:
        journal_id = env['account.journal'].create(journal_data)
        _logger.info(f"'Ledger Sync' journal created with ID {journal_id.id}")
        return journal_id
    except Exception as e:
        _logger.error(f"Failed to create 'Ledger Sync' journal: {str(e)}")
        return None


def _prepare_line_ids(payload, valid_account_ids, env):
    """Prepare the line items for the transaction."""
    lines = []

    # Prepare credit lines
    for credit in payload.get('credits', []):
        if credit['glAccountId'] in valid_account_ids:
            lines.append((0, 0, {
                'account_id': credit['glAccountId'],
                'credit': credit['amount'],
                'debit': 0
            }))

    # Prepare debit lines
    for debit in payload.get('debits', []):
        if debit['glAccountId'] in valid_account_ids:
            lines.append((0, 0, {
                'account_id': debit['glAccountId'],
                'credit': 0,
                'debit': debit['amount']
            }))

    return lines


def process_transaction(payload):
    """Process a transaction, including validation and posting to Odoo."""
    env = http.request.env
    is_valid, validation_error = validate_journal_entry(payload)

    if not is_valid:
        _logger.error(f"Payload validation failed: {validation_error}")
        return False

    currency_code = payload.get("currencyCode")
    currency_id = get_currency_id(currency_code)
    if not currency_id:
        _logger.error(f"Currency code {currency_code} not found.")
        return False

    credits = [credit.get("glAccountId") for credit in payload.get("credits", [])]
    debits = [debit.get("glAccountId") for debit in payload.get("debits", [])]

    if not credits or not debits:
        _logger.error("Payload missing required fields 'credits' or 'debits'")
        return False

    all_account_ids = set(credits + debits)
    _logger.info(f"All account IDs {all_account_ids}")

    valid_account_ids = validate_account_ids(env, all_account_ids)
    if len(valid_account_ids) != len(all_account_ids):
        _logger.error("One or more account IDs are invalid. Transaction will not be processed.")
        return False


    transaction_date_str = payload.get("transactionDate")
    try:
        transaction_date = datetime.datetime.strptime(transaction_date_str, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError as e:
        _logger.error(f"Invalid date format in transactionDate: {transaction_date_str}. Error: {e}")
        return False

    company_id = get_company_id(env)
    _logger.info(f"Company ID {company_id}")

    journal = create_or_get_ledger_sync_journal(env, company_id)
    if not journal:
        _logger.error("Failed to retrieve or create the 'Ledger Sync' journal.")
        return False
    transaction_reference = payload.get("transactionReference")
    existing_transaction = env["account.move"].search([
        ("ref", "=", transaction_reference)
    ], limit=1)

    if existing_transaction:
        error_message = f"Transaction with reference '{transaction_reference}' already exists."
        _logger.error(error_message)
        raise ValueError(error_message)  # Raise an exception if a duplicate is found

    _logger.info(f"Journal ID: {journal.id}, Journal Name: {journal.name}")

    line_ids = _prepare_line_ids(payload, valid_account_ids, env)

    transaction_data = {
        "journal_id": journal.id,
        "company_id": company_id,
        "date": transaction_date,
        "ref": payload.get("transactionReference"),
        "name": f"{journal.code}",
        "currency_id": currency_id,
        "line_ids": line_ids,
    }

    _logger.debug(f"Transaction data: {transaction_data}")

    try:
        transaction_id = env["account.move"].create(transaction_data)
        _logger.info(f"Transaction {transaction_id} created in Odoo")

        custom_journal_entry = env["custom.journal.entry"].create({
            "branch_id": payload.get("branchId"),
            "transaction_date": transaction_date,
            "transaction_reference": payload.get("transactionReference"),
            "time_stamp": payload.get("timeStamp"),
            "journal_id": journal.id,
            "company_id": company_id,
            "account_move_id": transaction_id.id,
            "currency_id": currency_id,
        })

        _logger.info(f"Custom journal entry created: {custom_journal_entry.id}")

        # Create journal entry lines
        for line in line_ids:
            amount = line[2]['credit'] if line[2]['credit'] > 0 else line[2]['debit']
            line_type = 'credit' if line[2]['credit'] > 0 else 'debit'

            env["custom.journal.entry.line"].create({
                'journal_entry_id': custom_journal_entry.id,
                'gl_account_id': line[2]['account_id'],
                'amount': amount,
                'type': line_type,
            })
            _logger.info(f"Created line for journal entry: {custom_journal_entry.id}, Account ID: {line[2]['account_id']}, Amount: {amount}, Type: {line_type}")

    except Exception as e:
        _logger.error(f"Error creating journal entry: {e}")
        return False

    return True


def update_journal_entry_in_database(payload):
    """Update a journal entry in the custom journal entry model in the database."""
    is_valid, validation_error = validate_journal_entry(payload)
    if not is_valid:
        _logger.error(f"Payload validation failed: {validation_error}")
        return {'status': 'error', 'message': validation_error}

    transaction_reference = payload.get('transactionReference')
    transaction_date_str = payload.get('transactionDate')

    # Parse transaction date
    try:
        transaction_date = datetime.datetime.strptime(transaction_date_str, '%d %B %Y').strftime('%Y-%m-%d')
    except ValueError as e:
        _logger.error(f"Invalid date format in transactionDate: {transaction_date_str}. Error: {e}")
        return {'status': 'error', 'message': 'Invalid transaction date format.'}

    env = request.env
    existing_entry = env['account.move'].sudo().search([('ref', '=', transaction_reference)], limit=1)

    if not existing_entry:
        _logger.error(f"No journal entry found with reference: {transaction_reference}")
        raise ValueError(f"No journal entry found with reference: {transaction_reference}")

    _logger.info(f"Journal entry found: {existing_entry.id}")

    total_debits = sum([debit.get('amount') for debit in payload.get('debits', [])])
    total_credits = sum([credit.get('amount') for credit in payload.get('credits', [])])

    if total_debits != total_credits:
        _logger.error(f"Debits and credits do not match. Total debits: {total_debits}, Total credits: {total_credits}")
        raise ValueError('Journal entry is not balanced')

    _logger.info("Amounts are balanced. Proceeding with update...")

    try:

        existing_entry.write({
            'date': transaction_date,
            'ref': transaction_reference,
            'narration': payload.get('comments'),
            'currency_id': get_currency_id(payload.get('currencyCode')),
        })
        _logger.info(f"Updated journal entry with date: {transaction_date}, reference: {transaction_reference}")

        custom_update_data = {
            "branch_id": payload.get("branchId"),
            "transaction_date": transaction_date,
            "time_stamp": payload.get("timeStamp"),
            "currency_id": get_currency_id(payload.get("currencyCode")),
        }
        _logger.debug(f"Custom journal entry update data: {custom_update_data}")

        try:
            custom_journal_entry = env['custom.journal.entry.model'].sudo().search([('ref', '=', transaction_reference)], limit=1)
            if custom_journal_entry:
                custom_journal_entry.write(custom_update_data)
                _logger.info(f"Custom journal entry {custom_journal_entry.id} updated successfully.")

                _logger.info(f"Unlinking existing custom debit and credit lines for entry: {custom_journal_entry.id}")
                custom_journal_entry.line_ids.unlink()

                for credit in payload.get('credits', []):
                    account_id = credit.get('glAccountId')
                    amount = credit.get('amount')
                    _logger.info(f"Creating custom credit line for account_id: {account_id} with amount: {amount}")
                    try:
                        env['custom.journal.entry.line'].sudo().create({
                            'journal_entry_id': custom_journal_entry.id,
                            'account_id': account_id,
                            'credit': amount,
                            'debit': 0
                        })
                    except Exception as e:
                        _logger.error(f"Error creating custom credit line for account_id: {account_id} with amount: {amount}. Error: {e}")


                for debit in payload.get('debits', []):
                    account_id = debit.get('glAccountId')
                    amount = debit.get('amount')
                    _logger.info(f"Creating custom debit line for account_id: {account_id} with amount: {amount}")
                    try:
                        env['custom.journal.entry.line'].sudo().create({
                            'journal_entry_id': custom_journal_entry.id,
                            'account_id': account_id,
                            'debit': amount,
                            'credit': 0
                        })
                    except Exception as e:
                        _logger.error(f"Error creating custom debit line for account_id: {account_id} with amount: {amount}. Error: {e}")
            else:
                _logger.warning("Custom journal entry not found; skipping update for custom model.")
        except Exception as e:
            _logger.error(f"Error updating custom journal entry fields: {e}", exc_info=True)
            # Do not raise, just log the error and continue

        # Unlink existing lines in Odoo
        existing_lines = existing_entry.line_ids
        _logger.info(f"Existing lines before unlinking: {[line.id for line in existing_lines]}")
        existing_entry.line_ids.unlink()
        _logger.info(f"Existing lines after unlinking: {[line.id for line in existing_entry.line_ids]}")

        # Create credit lines in Odoo
        for credit in payload.get('credits', []):
            account_id = credit.get('glAccountId')
            amount = credit.get('amount')
            _logger.info(f"Creating credit line for account_id: {account_id} with amount: {amount}")
            try:
                env['account.move.line'].sudo().create({
                    'move_id': existing_entry.id,
                    'account_id': account_id,
                    'credit': amount,
                    'debit': 0
                })
            except Exception as e:
                _logger.error(f"Error creating credit line for account_id: {account_id} with amount: {amount}. Error: {e}")

        # Create debit lines in Odoo
        for debit in payload.get('debits', []):
            account_id = debit.get('glAccountId')
            amount = debit.get('amount')
            _logger.info(f"Creating debit line for account_id: {account_id} with amount: {amount}")
            try:
                env['account.move.line'].sudo().create({
                    'move_id': existing_entry.id,
                    'account_id': account_id,
                    'debit': amount,
                    'credit': 0
                })
            except Exception as e:
                _logger.error(f"Error creating debit line for account_id: {account_id} with amount: {amount}. Error: {e}")

        # Check final balance in Odoo
        total_debits_existing = sum(line.debit for line in existing_entry.line_ids)
        total_credits_existing = sum(line.credit for line in existing_entry.line_ids)
        _logger.info(f"Final total debits: {total_debits_existing}")
        _logger.info(f"Final total credits: {total_credits_existing}")

        if total_debits_existing != total_credits_existing:
            _logger.error(f"Journal entry is not balanced. Total debits: {total_debits_existing}, Total credits: {total_credits_existing}")
            return {'status': 'error', 'message': 'Journal entry is not balanced'}

    except Exception as e:
        _logger.error(f"Error updating journal entry: {e}")
        return {'status': 'error', 'message': 'An error occurred while updating the journal entry'}

    return {'status': 'success', 'message': 'Journal entry updated successfully'}
