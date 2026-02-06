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
            
            # Create environment with empty context to avoid field resolution issues
            env = api.Environment(cr, SUPERUSER_ID, {})
            
            # Invalidate cache to prevent stale field definitions
            env.invalidate_all()
            
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
    # Use raw SQL to avoid field resolution issues with res.company model
    try:
        # Query the company table directly to avoid ORM field issues
        env.cr.execute("""
            SELECT id FROM res_company 
            WHERE active = true 
            ORDER BY id LIMIT 1
        """)
        result = env.cr.fetchone()
        if result:
            company_id = result[0]
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


def create_or_get_ledger_sync_journal(env, company_id, account_name=None):
    """Get or create journal based on account name.
    
    Uses account_name to create a journal-specific code and name.
    If account_name is None, defaults to 'Ledger Sync'.
    """
    if not account_name:
        account_name = 'Ledger Sync'
    
    # Create a short code from account name (max 5 chars for code field)
    # Use first 2 chars of account name + hash of full name for uniqueness
    journal_code = account_name[:2].upper()
    if len(account_name) > 2:
        journal_code += str(hash(account_name) % 1000).zfill(3)
    journal_code = journal_code[:5]  # Ensure max 5 chars
    
    try:
        # Search by code and company_id using raw SQL to avoid ORM field issues
        env.cr.execute("""
            SELECT id FROM account_journal 
            WHERE code = %s AND company_id = %s 
            LIMIT 1
        """, (journal_code, company_id))
        
        result = env.cr.fetchone()
        if result:
            journal = env['account.journal'].sudo().browse(result[0])
            _logger.info(f"Found existing journal with code '{journal_code}' and name '{account_name}' (ID {journal.id})")
            return journal
        
        # Create new journal using raw SQL to avoid ORM validation issues with parent_path
        import json
        from datetime import datetime
        
        env.cr.execute("""
            INSERT INTO account_journal 
            (code, company_id, type, name, active, create_uid, write_uid, create_date, write_date,
             invoice_reference_type, invoice_reference_model, multiple_invoice_type, text_position)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (journal_code, company_id, 'general', json.dumps({'en_US': account_name}), True, 1, 1,
              datetime.now(), datetime.now(), 'none', 'odoo', 'multi', 'after'))
        
        result = env.cr.fetchone()
        if result:
            journal_id = result[0]
            env.cr.commit()
            journal = env['account.journal'].sudo().browse(journal_id)
            _logger.info(f"Created new journal with code '{journal_code}' and name '{account_name}' (ID {journal.id})")
            return journal
        
        raise Exception("Failed to create journal - no ID returned")
    except Exception as e:
        _logger.error(f"Failed to get/create journal for account '{account_name}': {str(e)}")
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


def _create_account_move_lines(env, existing_entry, payload, id_mapping=None):
    """Create account move lines for both credits and debits.
    
    id_mapping: Optional dict mapping original account IDs to Odoo account IDs.
                If provided, uses mapped IDs; otherwise uses original IDs.
    """
    for credit in payload.get('credits', []):
        account_id = credit.get('glAccountId')
        # Use mapped account ID if available, otherwise use original
        if id_mapping and account_id in id_mapping:
            account_id = id_mapping[account_id]
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
        # Use mapped account ID if available, otherwise use original
        if id_mapping and account_id in id_mapping:
            account_id = id_mapping[account_id]
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


def _prepare_line_ids(payload, account_id_mapping, env):
    """Prepare the line items for the transaction.
    
    account_id_mapping: Dict mapping original account IDs to Odoo account IDs
    """
    lines = []

    # Prepare credit lines
    for credit in payload.get('credits', []):
        original_id = credit['glAccountId']
        if original_id in account_id_mapping:
            odoo_account_id = account_id_mapping[original_id]
            lines.append((0, 0, {
                'account_id': odoo_account_id,
                'credit': credit['amount'],
                'debit': 0
            }))

    # Prepare debit lines
    for debit in payload.get('debits', []):
        original_id = debit['glAccountId']
        if original_id in account_id_mapping:
            odoo_account_id = account_id_mapping[original_id]
            lines.append((0, 0, {
                'account_id': odoo_account_id,
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

    try:
        id_mapping = validate_account_ids(env, all_account_ids)
    except Exception as e:
        _logger.error(f"Error validating account IDs: {str(e)}")
        import traceback
        _logger.error(f"Traceback: {traceback.format_exc()}")
        return {'status': 'error', 'message': f"Error validating accounts: {str(e)}"}
    
    _logger.info(f"Account ID mapping: {id_mapping}")
    
    # Check if all accounts could be mapped
    unmapped_ids = all_account_ids - set(id_mapping.keys())
    if unmapped_ids:
        error_message = f"Account validation failed. These account IDs could not be found: {unmapped_ids}"
        _logger.error(error_message)
        return {'status': 'error', 'message': error_message}
    
    _logger.info(f"All accounts validated and mapped successfully")

    transaction_date_str = payload.get("transactionDate")
    try:
        transaction_date = datetime.strptime(transaction_date_str, "%d/%m/%Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError) as e:
        _logger.error(f"Invalid date format in transactionDate: {transaction_date_str}. Error: {e}")
        return {'status': 'error', 'message': "Invalid transaction date format"}

    company_id = get_company_id(env)
    _logger.info(f"Processing transaction {payload.get('transactionReference')}")

    # Get account name from the first account for journal naming
    account_name = None
    try:
        first_account_id = next(iter(all_account_ids))
        env.cr.execute("""
            SELECT account_name FROM custom_account_entry 
            WHERE account_code = %s
            LIMIT 1
        """, (str(first_account_id),))
        result = env.cr.fetchone()
        if result:
            account_name = result[0]
            _logger.debug(f"Retrieved account name '{account_name}' for journal creation")
    except Exception as e:
        _logger.warning(f"Could not retrieve account name for journal: {str(e)}")
    
    try:
        journal = create_or_get_ledger_sync_journal(env, company_id, account_name)
    except Exception as e:
        _logger.error(f"Failed to get journal: {str(e)}")
        return {'status': 'error', 'message': f"Failed to get journal: {str(e)}"}
    
    if not journal:
        return {'status': 'error', 'message': "No journal available"}

    transaction_reference = payload.get("transactionReference")
    
    # Check if transaction already exists using raw SQL
    try:
        env.cr.execute("""
            SELECT id FROM account_move 
            WHERE name = %s OR ref = %s
            LIMIT 1
        """, (transaction_reference, transaction_reference))
        existing_result = env.cr.fetchone()
        if existing_result:
            error_message = f"Transaction with reference '{transaction_reference}' already exists."
            _logger.error(error_message)
            return {'status': 'error', 'message': error_message}
    except Exception as e:
        _logger.warning(f"Could not check for existing transaction: {str(e)}")

    # Use journal ID and account name for transaction naming
    try:
        journal_id = journal.id
        # Use the account_name we already retrieved, or fallback to transaction reference
        journal_name = account_name if account_name else transaction_reference
    except Exception as e:
        _logger.error(f"Error accessing journal ID: {e}")
        return {'status': 'error', 'message': f"Error accessing journal: {str(e)}"}

    line_ids = _prepare_line_ids(payload, id_mapping, env)

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

    existing_entry = None
    try:
        env.cr.execute("""
            SELECT id FROM account_move 
            WHERE name = %s OR ref = %s
            LIMIT 1
        """, (transaction_reference, transaction_reference))
        result = env.cr.fetchone()
        if result:
            existing_entry = env['account.move'].sudo().browse(result[0])
    except Exception as e:
        _logger.error(f"Error searching for existing transaction: {str(e)}")

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
        
        # Validate and map account IDs for update
        all_account_ids = set([c.get('glAccountId') for c in payload.get('credits', [])] + 
                              [d.get('glAccountId') for d in payload.get('debits', [])])
        id_mapping = validate_account_ids(env, all_account_ids)
        if not id_mapping or len(id_mapping) != len(all_account_ids):
            unmapped = all_account_ids - set(id_mapping.keys())
            _logger.error(f"Account validation failed during update for IDs: {unmapped}")
            return {'status': 'error', 'message': f"Account validation failed for IDs: {unmapped}"}
        
        _create_account_move_lines(env, existing_entry, payload, id_mapping)

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
