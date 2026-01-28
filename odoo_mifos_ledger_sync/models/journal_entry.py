from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class CustomJournalEntry(models.Model):
    _name = 'custom.journal.entry'
    _description = 'Custom Journal Entry'

    branch_id = fields.Text(string='Branch ID')
    transaction_date = fields.Date(string='Transaction Date', required=True)
    transaction_reference = fields.Char(string='Transaction Reference', required=True)
    time_stamp = fields.Text(string='Timestamp')
    comments = fields.Text(string='Comments')
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)

    company_id = fields.Many2one('res.company', string='Company', required=True)

    account_move_id = fields.Many2one('account.move', string='Account Move', required=True)

    journal_id = fields.Many2one('account.journal', string='Journal', required=True)

    credit_ids = fields.One2many(
        'custom.journal.entry.line',
        'journal_entry_id',
        string='Credits',
        domain=[('type', '=', 'credit')]
    )
    debit_ids = fields.One2many(
        'custom.journal.entry.line',
        'journal_entry_id',
        string='Debits',
        domain=[('type', '=', 'debit')]
    )

    @api.model
    def create(self, vals):
        """
        Override the create method to validate currency_id and ensure it's provided.
        """
        if 'currency_id' not in vals or not vals['currency_id']:
            raise ValidationError(_("currency_id must be provided."))

        # Call the parent create method to create the journal entry
        return super(CustomJournalEntry, self).create(vals)


class CustomJournalEntryLine(models.Model):
    _name = 'custom.journal.entry.line'
    _description = 'Custom Journal Entry Line'

    journal_entry_id = fields.Many2one(
        'custom.journal.entry',
        string='Journal Entry',
        required=True
    )
    gl_account_id = fields.Many2one(
        'account.account',
        string='GL Account',
        required=True
    )
    amount = fields.Float(string='Amount', required=True)
    type = fields.Selection(
        [('credit', 'Credit'), ('debit', 'Debit')],
        string='Type',
        required=True
    )
