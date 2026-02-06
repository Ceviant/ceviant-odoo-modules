import logging
from datetime import datetime
from odoo import http
from odoo.http import request
from .validation import validate_journal_entry, validate_account_ids, get_default_currency, get_currency_id

_logger = logging.getLogger(__name__)


def get_env():
    """Get Odoo environment with proper database and user context."""
    try:
        return request.env
    except (RuntimeError, AttributeError):
        # Outside HTTP context (e.g., from RabbitMQ consumer thread)
        from odoo import api, SUPERUSER_ID
        from odoo.tools import config
        from odoo.modules import registry
        from odoo import sql_db
        
        db_name = config.get('db_name')
        if not db_name:
            _logger.error("No database configured")
            raise RuntimeError("Database not configured")
        
        cr = None
        try:
            # Get the registry for the database
            reg = registry.Registry(db_name)
            
            # Get database connection
            db_connection = sql_db.db_connect(db_name)
            
            # Create cursor
            cr = db_connection.cursor()
            
            # Create environment with empty context
            # Don't set company_id in context as it causes issues with res.users model
            env = api.Environment(cr, SUPERUSER_ID, {})
            
            _logger.info(f"✓ Successfully created environment for database: {db_name}")
            return env
        except Exception as e:
            _logger.error(f"Failed to initialize Odoo environment: {type(e).__name__}: {str(e)}")
            import traceback
            _logger.error(f"Traceback: {traceback.format_exc()}")
            # Explicitly close cursor if it was created
            if cr is not None:
                try:
                    cr.close()
                except Exception:
                    pass
            raise RuntimeError(f"Cannot create environment: {str(e)}")


def get_company_id(env):
    """Retrieve the company_id for the current context in Odoo."""
    # Explicitly search for the first company instead of using env.company
    # to avoid context resolution issues with res.users in RabbitMQ threads
    try:
        # Use search with empty domain to avoid field resolution issues
        # Note: Using empty search list [] is safer than specifying 'id' field in domain
        first_company = env['res.company'].sudo().search([])
        if first_company:
            company_id = first_company[0].id
            _logger.debug(f"Found company ID: {company_id}")
            return company_id
        else:
            _logger.error("No company found in the system")
            # Return default company ID instead of raising
            _logger.warning("Using default company ID: 1")
            return 1
    except Exception as e:
        _logger.error(f"Failed to get company ID: {str(e)}. Using default company ID: 1")
        # Return default instead of raising to prevent app shutdown
        return 1


def create_or_get_ledger_sync_journal(env, company_id):
    """Get or create 'Ledger Sync' journal."""
    try:
        # Try to find existing journal using sudo() to bypass access restrictions
        journal = env['account.journal'].sudo().search([
            ('company_id', '=', company_id),
            ('name', '=', 'Ledger Sync')
        ], limit=1)
        
        if journal:
            _logger.info(f"Found existing journal ID {journal.id}")
            return journal
        
        # Create new journal with sudo() context
        journal = env['account.journal'].sudo().create({
            'name': 'Ledger Sync',
            'company_id': company_id,
            'type': 'general'
        })
        _logger.info(f"Created new journal ID {journal.id}")
        return journal
    except Exception as e:
        _logger.error(f"Failed to get/create journal: {str(e)}")
        raise


def _create_custom_entry_lines(env, custom_journal_entry, payload):
    """Create custom journal entry lines for both credits and debits."""
    for credit in payload.get('credits', []):
        account_id = credit.get('glAccountId')
        amount = credit.get('amount')
        _logger.debug(f"Creating custom credit line for account_id: {account_id} with amount: {amount}")
        try:
            env['custom.journal.entry.line'].sudo().create({
                'journal_entry_id': custom_journal_entry.id,
                'gl_account_id': account_id,
                'amount': amount,
                'type': 'credit'
            })
        except Exception as e:
            _logger.error(f"Error creating custom credit line for account_id: {account_id}. Error: {e}")

    for debit in payload.get('debits', []):
        account_id = debit.get('glAccountId')
        amount = debit.get('amount')
        _logger.debug(f"Creating custom debit line for account_id: {account_id} with amount: {amount}")
        try:
            env['custom.journal.entry.line'].sudo().create({
                'journal_entry_id': custom_journal_entry.id,
                'gl_account_id': account_id,
                'amount': amount,
                'type': 'debit'
            })
        except Exception as e:
            _logger.error(f"Error creating custom debit line for account_id: {account_id}. Error: {e}")


def _create_account_move_lines(env, existing_entry, payload):
    """Create account move lines for both credits and debits."""
    for credit in payload.get('credits', []):
        account_id = credit.get('glAccountId')
        amount = credit.get('amount')
        _logger.debug(f"Creating credit line for account_id: {account_id} with amount: {amount}")
        try:
            env['account.move.line'].sudo().create({
                'move_id': existing_entry.id,
                'account_id': account_id,
                'credit': amount,
                'debit': 0
            })
        except Exception as e:
            _logger.error(f"Error creating credit line for account_id: {account_id}. Error: {e}")

    for debit in payload.get('debits', []):
        account_id = debit.get('glAccountId')
        amount = debit.get('amount')
        _logger.debug(f"Creating debit line for account_id: {account_id} with amount: {amount}")
        try:
            env['account.move.line'].sudo().create({
                'move_id': existing_entry.id,
                'account_id': account_id,
                'debit': amount,
                'credit': 0
            })
        except Exception as e:
            _logger.error(f"Error creating debit line for account_id: {account_id}. Error: {e}")


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
    """Process a transaction, including validation and posting to Odoo.
    
    Returns:
        dict: {'status': 'success'/'error', 'message': 'Description'}
    """
    try:
        env = get_env()
    except Exception as e:
        _logger.error(f"Failed to get environment: {e}")
        return {'status': 'error', 'message': f"Failed to get environment: {str(e)}"}
    
    is_valid, validation_error = validate_journal_entry(payload)

    if not is_valid:
        _logger.error(f"Payload validation failed: {validation_error}")
        return {'status': 'error', 'message': validation_error}

    currency_code = payload.get("currencyCode")
    currency_id = get_currency_id(env, currency_code)
    if not currency_id:
        _logger.error(f"Failed to get currency {currency_code} or default.")
        return {'status': 'error', 'message': f"Failed to get currency {currency_code}"}

    credits = [int(credit.get("glAccountId")) for credit in payload.get("credits", []) if credit.get("glAccountId") is not None]
    debits = [int(debit.get("glAccountId")) for debit in payload.get("debits", []) if debit.get("glAccountId") is not None]

    if not credits or not debits:
        _logger.error("Payload missing required fields 'credits' or 'debits'")
        return {'status': 'error', 'message': "Missing required fields 'credits' or 'debits'"}

    all_account_ids = set(credits + debits)
    _logger.info(f"All account IDs {all_account_ids}")

    valid_account_ids = validate_account_ids(env, all_account_ids)
    _logger.info(f"Valid account IDs found: {valid_account_ids}")
    
    # Log which accounts could not be validated
    invalid_ids = all_account_ids - valid_account_ids
    if invalid_ids:
        _logger.warning(f"Some account IDs could not be validated: {invalid_ids}. "
                       f"They may be custom account entries or need to be created.")
    
    # Use whatever valid IDs we found; if none exist, we'll still try to process
    # using the original IDs in case they represent custom accounts
    if valid_account_ids:
        account_ids_to_use = valid_account_ids
        _logger.info(f"Using {len(valid_account_ids)} validated account IDs")
    else:
        account_ids_to_use = all_account_ids
        _logger.warning(f"No account IDs could be validated. Will attempt to use all {len(all_account_ids)} account IDs as-is.")

    transaction_date_str = payload.get("transactionDate")
    try:
        transaction_date = datetime.strptime(transaction_date_str, "%d/%m/%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError) as e:
        _logger.error(f"Invalid date format in transactionDate: {transaction_date_str}. Error: {e}")
        return {'status': 'error', 'message': "Invalid transaction date format"}

    company_id = get_company_id(env)
    _logger.info(f"Processing transaction {payload.get('transactionReference')}")

    try:
        journal = create_or_get_ledger_sync_journal(env, company_id)
    except Exception as e:
        _logger.error(f"Failed to get journal: {str(e)}")
        return {'status': 'error', 'message': f"Failed to get 'Ledger Sync' journal: {str(e)}"}
    
    if not journal:
        return {'status': 'error', 'message': "No journal available"}

    transaction_reference = payload.get("transactionReference")
    existing_transaction = env["account.move"].search([
        ("ref", "=", transaction_reference)
    ], limit=1)

    if existing_transaction:
        error_message = f"Transaction with reference '{transaction_reference}' already exists."
        _logger.error(error_message)
        return {'status': 'error', 'message': error_message}

    # Safe access to journal attributes
    try:
        journal_id = journal.id
        journal_name = getattr(journal, 'name', f'Journal {journal_id}')
    except Exception as e:
        _logger.error(f"Error accessing journal attributes: {e}")
        return {'status': 'error', 'message': f"Error accessing journal: {str(e)}"}

    line_ids = _prepare_line_ids(payload, account_ids_to_use, env)

    transaction_data = {
        "journal_id": journal_id,
        "company_id": company_id,
        "date": transaction_date,
        "ref": payload.get("transactionReference"),
        "name": journal_name,
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
            "journal_id": journal_id,
            "company_id": company_id,
            "account_move_id": transaction_id.id,
            "currency_id": currency_id,
        })

        _logger.info(f"Custom journal entry created: {custom_journal_entry.id}")

        # Create journal entry lines
        for line in line_ids:
            line_data = line[2]
            amount = line_data['credit'] or line_data['debit']
            line_type = 'credit' if line_data['credit'] > 0 else 'debit'

            env["custom.journal.entry.line"].create({
                'journal_entry_id': custom_journal_entry.id,
                'gl_account_id': line_data['account_id'],
                'amount': amount,
                'type': line_type,
            })
            _logger.debug(f"Created line for journal entry: {custom_journal_entry.id}, Account ID: {line_data['account_id']}, Amount: {amount}, Type: {line_type}")

    except Exception as e:
        _logger.error(f"Error creating journal entry: {e}")
        import traceback
        _logger.error(f"Traceback: {traceback.format_exc()}")
        return {'status': 'error', 'message': f"Error creating journal entry: {str(e)}"}

    return {'status': 'success', 'message': 'Journal entry created successfully'}


def update_journal_entry_in_database(payload):
    """Update a journal entry in the custom journal entry model in the database."""
    try:
        env = get_env()
    except Exception as e:
        _logger.error(f"Failed to get environment: {e}")
        return {'status': 'error', 'message': f"Failed to get environment: {str(e)}"}
    
    is_valid, validation_error = validate_journal_entry(payload)
    if not is_valid:
        _logger.error(f"Payload validation failed: {validation_error}")
        return {'status': 'error', 'message': validation_error}

    transaction_reference = payload.get('transactionReference')
    transaction_date_str = payload.get('transactionDate')

    # Parse transaction date
    try:
        transaction_date = datetime.strptime(transaction_date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
    except (ValueError, TypeError) as e:
        _logger.error(f"Invalid date format in transactionDate: {transaction_date_str}. Error: {e}")
        return {'status': 'error', 'message': 'Invalid transaction date format.'}

    existing_entry = env['account.move'].sudo().search([('ref', '=', transaction_reference)], limit=1)

    if not existing_entry:
        _logger.error(f"No journal entry found with reference: {transaction_reference}")
        return {'status': 'error', 'message': f"No journal entry found with reference: {transaction_reference}"}

    _logger.info(f"Journal entry found: {existing_entry.id}")

    total_debits = sum([debit.get('amount') for debit in payload.get('debits', [])])
    total_credits = sum([credit.get('amount') for credit in payload.get('credits', [])])

    if total_debits != total_credits:
        _logger.error(f"Debits and credits do not match. Total debits: {total_debits}, Total credits: {total_credits}")
        return {'status': 'error', 'message': 'Journal entry is not balanced'}

    _logger.info("Amounts are balanced. Proceeding with update...")

    try:
        existing_entry.write({
            'date': transaction_date,
            'ref': transaction_reference,
            'narration': payload.get('comments'),
            'currency_id': get_currency_id(env, payload.get('currencyCode')),
        })
        _logger.info(f"Updated journal entry with date: {transaction_date}, reference: {transaction_reference}")

        currency_id = get_currency_id(env, payload.get("currencyCode"))
        custom_update_data = {
            "branch_id": payload.get("branchId"),
            "transaction_date": transaction_date,
            "time_stamp": payload.get("timeStamp"),
            "currency_id": currency_id,
        }
        _logger.debug(f"Custom journal entry update data: {custom_update_data}")

        custom_journal_entry = env['custom.journal.entry'].sudo().search([('transaction_reference', '=', transaction_reference)], limit=1)
        if custom_journal_entry:
            custom_journal_entry.write(custom_update_data)
            _logger.info(f"Custom journal entry {custom_journal_entry.id} updated successfully.")
            _logger.debug(f"Unlinking existing custom lines for entry: {custom_journal_entry.id}")
            custom_journal_entry.line_ids.unlink()
            _create_custom_entry_lines(env, custom_journal_entry, payload)
        else:
            _logger.warning("Custom journal entry not found; skipping update for custom model.")

        # Unlink existing lines in Odoo
        _logger.debug(f"Unlinking existing move lines for entry: {existing_entry.id}")
        existing_entry.line_ids.unlink()
        _create_account_move_lines(env, existing_entry, payload)

        # Check final balance
        total_debits_existing = sum(line.debit for line in existing_entry.line_ids)
        total_credits_existing = sum(line.credit for line in existing_entry.line_ids)
        _logger.debug(f"Final balance - Debits: {total_debits_existing}, Credits: {total_credits_existing}")

        if total_debits_existing != total_credits_existing:
            _logger.error(f"Journal entry is not balanced. Debits: {total_debits_existing}, Credits: {total_credits_existing}")
            return {'status': 'error', 'message': 'Journal entry is not balanced'}

    except Exception as e:
        _logger.error(f"Error updating journal entry: {e}", exc_info=True)
        return {'status': 'error', 'message': 'An error occurred while updating the journal entry'}

    return {'status': 'success', 'message': 'Journal entry updated successfully'}
