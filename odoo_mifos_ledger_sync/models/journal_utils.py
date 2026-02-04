import logging
import datetime
from odoo import http
from odoo.http import request
from .validation import validate_journal_entry, validate_account_ids, get_currency_id

_logger = logging.getLogger(__name__)

# Cache for journals and currencies
_journal_cache = {}
_currency_cache = {}

def get_env():
    """Get Odoo environment with proper database and user context."""
    try:
        return request.env
    except (RuntimeError, AttributeError):
        # Outside HTTP context (e.g., from RabbitMQ consumer thread)
        from odoo import api, SUPERUSER_ID
        from odoo.tools import config
        from odoo.modules import registry
        
        db_name = config.get('db_name')
        if not db_name:
            _logger.error("No database configured")
            raise RuntimeError("Database not configured")
        
        try:
            # Get the registry for the database
            reg = registry.Registry(db_name)
            
            # Get database connection
            db_connection = api.sql_db.db_connect(db_name)
            if not db_connection:
                _logger.error(f"Failed to connect to database: {db_name}")
                raise RuntimeError(f"Cannot connect to database: {db_name}")
            
            # Create cursor
            cr = db_connection.cursor()
            if not cr:
                _logger.error(f"Failed to create database cursor for: {db_name}")
                raise RuntimeError(f"Cannot create cursor for database: {db_name}")
            
            # Create environment
            env = api.Environment(cr, SUPERUSER_ID, {})
            if not env:
                _logger.error("Failed to create Odoo environment")
                raise RuntimeError("Cannot create Odoo environment")
            
            _logger.info(f"✓ Successfully created environment for database: {db_name}")
            return env
        except Exception as e:
            _logger.error(f"Failed to initialize Odoo environment: {type(e).__name__}: {str(e)}")
            import traceback
            _logger.error(f"Traceback: {traceback.format_exc()}")
            raise RuntimeError(f"Cannot create environment: {str(e)}")


def get_company_id(env):
    """Retrieve the company_id - use SUPERUSER if no user context."""
    try:
        if env.user and env.user.id:
            return env.user.company_id.id
    except:
        pass
    # Fallback: get first active company
    return env['res.company'].search([], limit=1).id


def create_or_get_ledger_sync_journal(env, company_id):
    """Check if 'Ledger Sync' journal exists, otherwise create it (cached)."""
    cache_key = f"ledger_{company_id}"
    if cache_key in _journal_cache:
        return _journal_cache[cache_key]
    
    journal_code = 'LEDGE'
    existing_journal = env['account.journal'].search([
        ('code', '=', journal_code),
        ('company_id', '=', company_id)
    ], limit=1)

    if existing_journal:
        _journal_cache[cache_key] = existing_journal[0]
        return existing_journal[0]

    journal_id = env['account.journal'].create({
        'name': 'Ledger Sync',
        'type': 'general',
        'code': journal_code,
        'company_id': company_id,
    })
    _journal_cache[cache_key] = journal_id
    _logger.info(f"Created journal {journal_id.id}")
    return journal_id


def _prepare_line_ids(payload, valid_account_ids):
    """Prepare line items in batch (faster)."""
    lines = []
    
    # Credits
    for credit in payload.get('credits', []):
        if credit['glAccountId'] in valid_account_ids:
            lines.append((0, 0, {
                'account_id': credit['glAccountId'],
                'credit': credit['amount'],
                'debit': 0
            }))
    
    # Debits
    for debit in payload.get('debits', []):
        if debit['glAccountId'] in valid_account_ids:
            lines.append((0, 0, {
                'account_id': debit['glAccountId'],
                'credit': 0,
                'debit': debit['amount']
            }))
    
    return lines


def _parse_transaction_date(date_str):
    """Parse transaction date once."""
    try:
        return datetime.datetime.strptime(date_str, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def process_transaction(payload):
    """Process transaction with batch operations and detailed error logging."""
    try:
        _logger.info(f"Step 1: Getting environment...")
        env = get_env()
        if not env:
            _logger.error(f"✗ Environment is None")
            return False
        _logger.info(f"✓ Got environment")
        
        _logger.info(f"Step 2: Validating payload...")
        is_valid, error = validate_journal_entry(payload)
        if not is_valid:
            _logger.error(f"✗ Validation failed: {error}")
            return False
        _logger.info(f"✓ Validation passed")
        
        _logger.info(f"Step 3: Parsing transaction date...")
        trans_date = _parse_transaction_date(payload.get("transactionDate"))
        if not trans_date:
            _logger.error(f"✗ Invalid date: {payload.get('transactionDate')}")
            return False
        _logger.info(f"✓ Date parsed: {trans_date}")
        
        _logger.info(f"Step 4: Getting currency...")
        currency_id = get_currency_id(payload.get("currencyCode"))
        if not currency_id:
            _logger.error(f"✗ Invalid currency: {payload.get('currencyCode')}")
            return False
        _logger.info(f"✓ Currency ID: {currency_id}")
        
        _logger.info(f"Step 5: Extracting account IDs...")
        credits = [c.get("glAccountId") for c in payload.get("credits", [])]
        debits = [d.get("glAccountId") for d in payload.get("debits", [])]
        
        if not credits or not debits:
            _logger.error("✗ Missing credits or debits")
            return False
        _logger.info(f"✓ Credits: {credits}, Debits: {debits}")
        
        _logger.info(f"Step 6: Validating accounts...")
        all_accounts = set(credits + debits)
        valid_accounts = validate_account_ids(env, all_accounts)
        if len(valid_accounts) != len(all_accounts):
            _logger.error(f"✗ Invalid account IDs. Expected {len(all_accounts)}, got {len(valid_accounts)}")
            _logger.error(f"  All accounts: {all_accounts}")
            _logger.error(f"  Valid accounts: {valid_accounts}")
            return False
        _logger.info(f"✓ All accounts valid")
        
        _logger.info(f"Step 7: Checking for duplicate transaction...")
        trans_ref = payload.get("transactionReference")
        existing = env["account.move"].search([("ref", "=", trans_ref)])
        if existing:
            _logger.error(f"✗ Duplicate transaction: {trans_ref}")
            return False
        _logger.info(f"✓ No duplicate found")
        
        _logger.info(f"Step 8: Getting company ID...")
        company_id = get_company_id(env)
        if not company_id:
            _logger.error("✗ Failed to get company ID")
            return False
        _logger.info(f"✓ Company ID: {company_id}")
        
        _logger.info(f"Step 9: Getting/creating journal...")
        journal = create_or_get_ledger_sync_journal(env, company_id)
        if not journal:
            _logger.error("✗ Failed to get journal")
            return False
        _logger.info(f"✓ Journal ID: {journal.id}")
        
        _logger.info(f"Step 10: Preparing line items...")
        line_ids = _prepare_line_ids(payload, valid_accounts)
        if not line_ids:
            _logger.error("✗ No valid line items prepared")
            return False
        _logger.info(f"✓ Prepared {len(line_ids)} lines")
        
        _logger.info(f"Step 11: Creating account.move...")
        move = env["account.move"].create({
            "journal_id": journal.id,
            "company_id": company_id,
            "date": trans_date,
            "ref": trans_ref,
            "name": journal.code,
            "currency_id": currency_id,
            "line_ids": line_ids,
        })
        if not move:
            _logger.error("✗ Failed to create account.move")
            return False
        _logger.info(f"✓ Move {move.id} created")
        
        _logger.info(f"Step 12: Creating custom.journal.entry...")
        custom_entry = env["custom.journal.entry"].create({
            "branch_id": payload.get("branchId"),
            "transaction_date": trans_date,
            "transaction_reference": trans_ref,
            "time_stamp": payload.get("timeStamp"),
            "journal_id": journal.id,
            "company_id": company_id,
            "account_move_id": move.id,
            "currency_id": currency_id,
        })
        if not custom_entry:
            _logger.error("✗ Failed to create custom.journal.entry")
            return False
        _logger.info(f"✓ Custom entry {custom_entry.id} created")
        
        _logger.info(f"Step 13: Creating custom.journal.entry.line records...")
        custom_lines = []
        for line in line_ids:
            account_id = line[2]['account_id']
            amount = line[2]['credit'] if line[2]['credit'] > 0 else line[2]['debit']
            line_type = 'credit' if line[2]['credit'] > 0 else 'debit'
            custom_lines.append({
                'journal_entry_id': custom_entry.id,
                'gl_account_id': account_id,
                'amount': amount,
                'type': line_type,
            })
        
        if custom_lines:
            env["custom.journal.entry.line"].create(custom_lines)
            _logger.info(f"✓ Created {len(custom_lines)} custom lines")
        
        _logger.info(f"✓ Transaction {trans_ref} processed successfully")
        return True
        
    except Exception as e:
        _logger.error(f"✗ Exception in process_transaction: {type(e).__name__}: {str(e)}")
        import traceback
        _logger.error(f"Traceback:\n{traceback.format_exc()}")
        return False


def update_journal_entry_in_database(payload):
    """Update journal entry with batch operations and no unnecessary logging."""
    try:
        is_valid, error = validate_journal_entry(payload)
        if not is_valid:
            _logger.error(f"Validation failed: {error}")
            return False
        
        trans_date = _parse_transaction_date(payload.get('transactionDate'))
        if not trans_date:
            _logger.error(f"Invalid date: {payload.get('transactionDate')}")
            return False
        
        env = get_env()
        if not env:
            _logger.error("Failed to get environment")
            return False
        
        trans_ref = payload.get('transactionReference')
        
        # Find existing entry
        existing_entry = env['account.move'].search([('ref', '=', trans_ref)], limit=1)
        if not existing_entry:
            _logger.error(f"Transaction not found: {trans_ref}")
            return False
        
        # Verify balance
        total_debits = sum(d.get('amount', 0) for d in payload.get('debits', []))
        total_credits = sum(c.get('amount', 0) for c in payload.get('credits', []))
        
        if total_debits != total_credits:
            _logger.error(f"Unbalanced: debits={total_debits} credits={total_credits}")
            return False
        
        try:
            # Batch update Odoo entry
            existing_entry.write({
                'date': trans_date,
                'ref': trans_ref,
                'narration': payload.get('comments'),
                'currency_id': get_currency_id(payload.get('currencyCode')),
            })
            
            # Batch delete and recreate lines (faster than update)
            existing_entry.line_ids.unlink()
            
            # Prepare all new lines at once
            new_lines = []
            for credit in payload.get('credits', []):
                new_lines.append((0, 0, {
                    'account_id': credit.get('glAccountId'),
                    'credit': credit.get('amount'),
                    'debit': 0
                }))
            for debit in payload.get('debits', []):
                new_lines.append((0, 0, {
                    'account_id': debit.get('glAccountId'),
                    'credit': 0,
                    'debit': debit.get('amount')
                }))
            
            # Create all at once
            if new_lines:
                existing_entry.line_ids = new_lines
            
            # Update custom entry (batch)
            custom_entry = env['custom.journal.entry'].search([('transaction_reference', '=', trans_ref)], limit=1)
            if custom_entry:
                custom_entry.write({
                    "branch_id": payload.get("branchId"),
                    "transaction_date": trans_date,
                    "time_stamp": payload.get("timeStamp"),
                    "currency_id": get_currency_id(payload.get("currencyCode")),
                })
                
                # Batch delete and recreate custom lines
                custom_entry.line_ids.unlink()
                
                custom_lines = []
                for credit in payload.get('credits', []):
                    custom_lines.append({
                        'journal_entry_id': custom_entry.id,
                        'gl_account_id': credit.get('glAccountId'),
                        'amount': credit.get('amount'),
                        'type': 'credit'
                    })
                for debit in payload.get('debits', []):
                    custom_lines.append({
                        'journal_entry_id': custom_entry.id,
                        'gl_account_id': debit.get('glAccountId'),
                        'amount': debit.get('amount'),
                        'type': 'debit'
                    })
                
                if custom_lines:
                    env['custom.journal.entry.line'].create(custom_lines)
            
            _logger.info(f"✓ Updated {trans_ref}")
            return True
            
        except Exception as e:
            _logger.error(f"Update failed: {type(e).__name__}: {str(e)}")
            import traceback
            _logger.error(f"Traceback: {traceback.format_exc()}")
            return False
            
    except Exception as e:
        _logger.error(f"Update function failed: {type(e).__name__}: {str(e)}")
        import traceback
        _logger.error(f"Traceback: {traceback.format_exc()}")
        return False
